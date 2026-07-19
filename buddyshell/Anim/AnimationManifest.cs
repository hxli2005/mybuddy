using System.IO;
using System.Text.RegularExpressions;

namespace BuddyShell.Anim;

public sealed partial class AnimationManifest
{
    private static readonly string[] RequiredPlanIds =
    [
        "idle.default.normal", "think.normal", "touch.head.normal", "touch.body.happy",
        "speech.neutral", "speech.happy",
        "edge.left.normal", "edge.right.normal", "edge.left.rise.normal", "edge.right.rise.normal",
    ];
    private readonly Dictionary<string, AnimationPlan> _plans;
    private readonly Dictionary<AnimationIntent, AnimationPlan> _plansByIntent;

    private AnimationManifest(string petRoot, IEnumerable<AnimationPlan> plans)
    {
        PetRoot = Path.GetFullPath(petRoot);
        _plans = plans.ToDictionary(plan => plan.Id, StringComparer.Ordinal);
        _plansByIntent = _plans.Values.ToDictionary(plan => plan.Intent);
    }

    public string PetRoot { get; }
    public IReadOnlyDictionary<string, AnimationPlan> Plans => _plans;

    public static AnimationManifest CreateDefault(string petRoot)
    {
        var manifest = new AnimationManifest(petRoot, new Builder(petRoot).Build());
        var errors = manifest.Validate();
        if (errors.Count > 0) throw new InvalidDataException(string.Join("\n", errors));
        return manifest;
    }

    public static AnimationManifest Create(
        string petRoot,
        IEnumerable<AnimationPlan> plans,
        bool validate = true)
    {
        var manifest = new AnimationManifest(petRoot, plans);
        var errors = validate ? manifest.Validate() : [];
        if (errors.Count > 0) throw new InvalidDataException(string.Join("\n", errors));
        return manifest;
    }

    public AnimationPlan Resolve(AnimationRequest request) =>
        _plansByIntent.TryGetValue(request.Intent, out var plan)
            ? plan
            : throw new KeyNotFoundException($"动画 intent 不存在：{request.Intent}");

    public AnimationPlan Get(string id) => _plans.TryGetValue(id, out var plan)
        ? plan
        : throw new KeyNotFoundException($"动画 plan 不存在：{id}");

    public IReadOnlyList<string> Validate()
    {
        var required = RequiredPlanIds.Concat(
            BodyActionCatalog.Default.Actions.SelectMany(action =>
                action.Animations.Select(animation => animation.PlanId)));
        var errors = required.Where(id => !_plans.ContainsKey(id))
            .Select(id => $"缺少必需 plan：{id}").ToList();
        foreach (var plan in _plans.Values)
        {
            foreach (var phase in new[] { plan.Entry, plan.Body, plan.Exit }.OfType<AnimationPhasePlan>())
            {
                if (phase.Layers.Count == 0) errors.Add($"{plan.Id}/{phase.Kind}: 没有 layer");
                foreach (var frame in phase.Layers.SelectMany(layer => layer.Frames))
                {
                    if (!File.Exists(frame.Path)) errors.Add($"{plan.Id}: 文件不存在 {frame.Path}");
                    if (frame.DurationMs <= 0) errors.Add($"{plan.Id}: 非法帧时长 {frame.Path}");
                }
            }
            if ((plan.IsBaseline || plan.IsPending) && plan.Id != "idle.default.normal" &&
                (plan.Entry is null || plan.Exit is null || !plan.Body.Loop))
            {
                errors.Add($"{plan.Id}: 持续动画必须具备 A/B(loop)/C");
            }
        }
        return errors;
    }

    private sealed class Builder(string petRoot)
    {
        private readonly string _root = Path.GetFullPath(petRoot);

        public IReadOnlyList<AnimationPlan> Build()
        {
            var activityPlans = BodyActionCatalog.Default.Actions
                .SelectMany(action => action.Animations.Select(animation =>
                    Action(animation, action.Shape)))
                .ToArray();
            return
            [
                Baseline("idle.default.normal", AnimationIntent.Idle, null, "Default/Nomal/1", null),
                Baseline("edge.left.normal", AnimationIntent.EdgeLeft, "SideHide_Left_Main/Nomal/A", "SideHide_Left_Main/Nomal/B_1", "SideHide_Left_Main/Nomal/C"),
                Baseline("edge.right.normal", AnimationIntent.EdgeRight, "SideHide_Right_Main/Nomal/A", "SideHide_Right_Main/Nomal/B_1", "SideHide_Right_Main/Nomal/C"),
                Transient("edge.left.rise.normal", AnimationIntent.EdgeLeftRise, "SideHide_Left_Rise/Nomal/A", "SideHide_Left_Rise/Nomal/B", "SideHide_Left_Rise/Nomal/C"),
                Transient("edge.right.rise.normal", AnimationIntent.EdgeRightRise, "SideHide_Right_Rise/Nomal/A", "SideHide_Right_Rise/Nomal/B", "SideHide_Right_Rise/Nomal/C"),
                ..activityPlans,
                Pending("think.normal", AnimationIntent.Think, "Think/Nomal/A", "Think/Nomal/B", "Think/Nomal/C"),
                Transient("touch.head.normal", AnimationIntent.TouchHeadReflex, "Touch_Head/A_Nomal", "Touch_Head/B_Nomal", "Touch_Head/C_Nomal"),
                Transient("touch.body.happy", AnimationIntent.TouchBodyReflex, "Touch_Body/A_Happy/tb1", "Touch_Body/B_Happy/tb1", "Touch_Body/C_Happy/tb1"),
                Transient("speech.neutral", AnimationIntent.Neutral, "Say/Self/A", "Say/Self/B_1", "Say/Self/C"),
                Transient("speech.happy", AnimationIntent.Happy, "Say/Shining/A", "Say/Shining/B_1", "Say/Shining/C"),
            ];
        }

        private AnimationPlan Action(BodyActionAnimation animation, BodyActionShape shape) =>
            new(
                animation.PlanId,
                animation.Intent,
                SequencePhase(animation.Entry, AnimationPhaseKind.Entry),
                Phase(animation.Body, AnimationPhaseKind.Body, shape == BodyActionShape.Interactive)!,
                Phase(animation.Exit, AnimationPhaseKind.Exit));

        private AnimationPlan Baseline(string id, AnimationIntent intent, string? entry, string body, string? exit) =>
            new(id, intent, Phase(entry, AnimationPhaseKind.Entry), Phase(body, AnimationPhaseKind.Body, true)!, Phase(exit, AnimationPhaseKind.Exit), true);

        private AnimationPlan Pending(string id, AnimationIntent intent, string entry, string body, string exit) =>
            new(id, intent, Phase(entry, AnimationPhaseKind.Entry), Phase(body, AnimationPhaseKind.Body, true)!, Phase(exit, AnimationPhaseKind.Exit), false, true);

        private AnimationPlan Transient(string id, AnimationIntent intent, string entry, string body, string exit) =>
            new(id, intent, Phase(entry, AnimationPhaseKind.Entry), Phase(body, AnimationPhaseKind.Body)!, Phase(exit, AnimationPhaseKind.Exit));

        private AnimationPhasePlan? Phase(string? relative, AnimationPhaseKind kind, bool loop = false) =>
            relative is null ? null : new(kind, loop, [Layer(relative)]);

        private AnimationPhasePlan SequencePhase(
            IReadOnlyList<string> relatives,
            AnimationPhaseKind kind) =>
            new(kind, false, [new AnimationLayerPlan(
                "main", 0, relatives.SelectMany(Frames).ToArray())]);

        private AnimationLayerPlan Layer(string relative) =>
            new("main", 0, Frames(relative));

        private IReadOnlyList<AnimationFrameSpec> Frames(string relative)
        {
            var folder = Path.Combine(_root, relative.Replace('/', Path.DirectorySeparatorChar));
            if (!Directory.Exists(folder)) throw new DirectoryNotFoundException($"动画目录不存在：{relative}");
            return Directory.EnumerateFiles(folder, "*.png", SearchOption.TopDirectoryOnly)
                .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
                .Select(path => new AnimationFrameSpec(path, ParseDuration(path))).ToArray();
        }

        private static int ParseDuration(string path)
        {
            var match = DurationPattern().Match(Path.GetFileNameWithoutExtension(path));
            if (!match.Success || !int.TryParse(match.Groups[1].Value, out var duration) || duration <= 0)
                throw new InvalidDataException($"动画帧文件名缺少合法时长：{path}");
            return duration;
        }
    }

    [GeneratedRegex(@"_(\d+)$")]
    private static partial Regex DurationPattern();
}

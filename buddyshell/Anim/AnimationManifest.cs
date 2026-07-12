using System.IO;
using System.Text.RegularExpressions;

namespace BuddyShell.Anim;

public sealed partial class AnimationManifest
{
    private static readonly string[] FoodIds = ["congee", "curry", "milk_tea", "coffee", "water"];
    private static readonly string[] RequiredPlanIds =
    [
        "idle.default.normal", "sleep.normal", "work.write.normal", "idle.read.normal",
        "idle.write.normal", "idle.gaze.normal", "idle.stretch.normal", "think.normal",
        "touch.head.normal", "touch.body.happy", "speech.neutral", "speech.happy",
        "speech.alert", "speech.worried", "feed.eat.normal", "feed.drink.normal",
    ];
    private readonly string _petRoot;
    private readonly Dictionary<string, AnimationPlan> _plans;

    private AnimationManifest(string petRoot, IEnumerable<AnimationPlan> plans)
    {
        _petRoot = Path.GetFullPath(petRoot);
        _plans = plans.ToDictionary(plan => plan.Id, StringComparer.Ordinal);
    }

    public string PetRoot => _petRoot;
    public IReadOnlyDictionary<string, AnimationPlan> Plans => _plans;

    public static AnimationManifest CreateDefault(string petRoot)
    {
        var builder = new Builder(petRoot);
        var manifest = new AnimationManifest(petRoot, builder.Build());
        var errors = manifest.Validate();
        if (errors.Count > 0)
        {
            throw new InvalidDataException("动画 manifest 无效：\n" + string.Join("\n", errors));
        }
        return manifest;
    }

    public static AnimationManifest Create(
        string petRoot,
        IEnumerable<AnimationPlan> plans,
        bool validate = true)
    {
        var manifest = new AnimationManifest(petRoot, plans);
        var errors = validate ? manifest.Validate() : [];
        if (errors.Count > 0)
        {
            throw new InvalidDataException("动画 manifest 无效：\n" + string.Join("\n", errors));
        }
        return manifest;
    }

    public AnimationPlan ResolveBaseline(BaselineSnapshot snapshot)
    {
        if (snapshot.Sleeping) return Get("sleep.normal");
        if (snapshot.WorkSessionActive) return Get("work.write.normal");
        return snapshot.IdleHint.Trim().ToLowerInvariant() switch
        {
            "read" => Get("idle.read.normal"),
            "write" => Get("idle.write.normal"),
            "gaze" => Get("idle.gaze.normal"),
            _ => Get("idle.default.normal"),
        };
    }

    public AnimationPlan Resolve(AnimationRequest request)
    {
        if (request.Intent == AnimationIntent.Eat)
        {
            var requestedItem = request.Payload is not null && request.Payload.TryGetValue("item", out var value)
                ? value
                : "water";
            var item = FoodIds.Contains(requestedItem, StringComparer.Ordinal) ? requestedItem : "water";
            var drink = item is "milk_tea" or "coffee" or "water";
            return WithFoodLayer(Get(drink ? "feed.drink.normal" : "feed.eat.normal"), item);
        }
        var id = request.Intent switch
        {
            AnimationIntent.Idle => "idle.default.normal",
            AnimationIntent.Sleep or AnimationIntent.Nap => "sleep.normal",
            AnimationIntent.Work => "work.write.normal",
            AnimationIntent.Read => "idle.read.normal",
            AnimationIntent.Write => "idle.write.normal",
            AnimationIntent.Gaze => "idle.gaze.normal",
            AnimationIntent.Stretch => "idle.stretch.normal",
            AnimationIntent.Think => "think.normal",
            AnimationIntent.TouchHeadReflex => "touch.head.normal",
            AnimationIntent.TouchBodyReflex => "touch.body.happy",
            AnimationIntent.Happy => "speech.happy",
            AnimationIntent.Alert => "speech.alert",
            AnimationIntent.Sad or AnimationIntent.Worried => "speech.worried",
            _ => "speech.neutral",
        };
        return Get(id);
    }

    public AnimationPlan Get(string id) => _plans.TryGetValue(id, out var plan)
        ? plan
        : throw new KeyNotFoundException($"动画 plan 不存在：{id}");

    public IReadOnlyList<string> Validate()
    {
        var errors = new List<string>();
        foreach (var required in RequiredPlanIds.Where(id => !_plans.ContainsKey(id)))
        {
            errors.Add($"缺少必需 plan：{required}");
        }
        foreach (var plan in _plans.Values)
        {
            foreach (var phase in new[] { plan.Entry, plan.Body, plan.Exit }.OfType<AnimationPhasePlan>())
            {
                if (phase.Layers.Count == 0) errors.Add($"{plan.Id}/{phase.Kind}: 没有 layer");
                foreach (var layer in phase.Layers)
                {
                    if (layer.Frames.Count == 0) errors.Add($"{plan.Id}/{phase.Kind}/{layer.Name}: 没有 frame");
                    foreach (var frame in layer.Frames)
                    {
                        if (!File.Exists(frame.Path)) errors.Add($"{plan.Id}: 文件不存在 {frame.Path}");
                        if (frame.DurationMs <= 0) errors.Add($"{plan.Id}: 非法帧时长 {frame.Path}");
                    }
                }
                if (phase.Layers.Count > 1 && phase.Layers.Select(layer => layer.DurationMs).Distinct().Count() != 1)
                {
                    errors.Add($"{plan.Id}/{phase.Kind}: 动画层时长不一致");
                }
                if (phase.Loop && phase.DurationMs is < 100 or > 10_000)
                {
                    errors.Add($"{plan.Id}/{phase.Kind}: loop 时长超出合理范围 {phase.DurationMs}ms");
                }
            }
            if (plan.IsBaseline && plan.Id != "idle.default.normal" &&
                (plan.Entry is null || plan.Exit is null || !plan.Body.Loop))
            {
                errors.Add($"{plan.Id}: phased baseline 必须具备 A/B(loop)/C");
            }
            if (plan.IsPending && (plan.Entry is null || plan.Exit is null || !plan.Body.Loop))
            {
                errors.Add($"{plan.Id}: pending plan 必须具备 A/B(loop)/C");
            }
            if (plan.Id.StartsWith("feed.", StringComparison.Ordinal) &&
                (plan.ItemMotion is null || plan.ItemMotion.Count == 0))
            {
                errors.Add($"{plan.Id}: 缺少显式食物运动轨迹");
            }
            if (plan.ItemMotion?.Any(frame => frame.DurationMs <= 0) == true)
            {
                errors.Add($"{plan.Id}: 食物运动轨迹存在非法时长");
            }
        }
        foreach (var itemId in FoodIds)
        {
            if (AssetLocator.FindFoodImage(_petRoot, itemId) is null)
            {
                errors.Add($"食物图片不存在：{itemId}");
            }
        }
        return errors;
    }

    private AnimationPlan WithFoodLayer(AnimationPlan basePlan, string itemId)
    {
        var image = AssetLocator.FindFoodImage(_petRoot, itemId)
            ?? throw new FileNotFoundException($"找不到食物图片：{itemId}");
        var body = basePlan.Body;
        var itemFrames = basePlan.ItemMotion is { Count: > 0 } motion
            ? motion.Select(frame => new AnimationFrameSpec(
                image,
                frame.DurationMs,
                frame.Placement,
                frame.Rotation,
                frame.Opacity,
                frame.Visible)).ToArray()
            : [new AnimationFrameSpec(image, body.DurationMs, new LayerPlacement(96, 96, 0, -16))];
        var itemLayer = new AnimationLayerPlan("item", 1, itemFrames);
        return basePlan with
        {
            Id = $"{basePlan.Id}:{itemId}",
            Body = body with { Layers = body.Layers.Append(itemLayer).OrderBy(layer => layer.ZIndex).ToArray() },
        };
    }

    private sealed class Builder(string petRoot)
    {
        private readonly string _root = Path.GetFullPath(petRoot);

        public IReadOnlyList<AnimationPlan> Build() =>
        [
            Baseline("idle.default.normal", AnimationIntent.Idle, null, "Default/Nomal/1", null),
            Baseline("sleep.normal", AnimationIntent.Sleep, "Sleep/A_Nomal", "Sleep/B_Nomal", "Sleep/C_Nomal"),
            Baseline("work.write.normal", AnimationIntent.Work, "WORK/WorkONE/A_Nomal", "WORK/WorkONE/B_1_Nomal", "WORK/WorkONE/C_Nomal"),
            Baseline("idle.read.normal", AnimationIntent.Read, "WORK/Study/A_Nomal", "WORK/Study/B_1_Nomal", "WORK/Study/C_Nomal"),
            Baseline("idle.write.normal", AnimationIntent.Write, "WORK/Calligraphy/Nomal/A", "WORK/Calligraphy/Nomal/B", "WORK/Calligraphy/Nomal/C"),
            Baseline("idle.gaze.normal", AnimationIntent.Gaze, "IDEL/aside/Nomal/A", "IDEL/aside/Nomal/B", "IDEL/aside/Nomal/C"),
            Transient("idle.stretch.normal", AnimationIntent.Stretch, null, "IDEL/yawning/Nomal", null),
            Pending("think.normal", AnimationIntent.Think, "Think/Nomal/A", "Think/Nomal/B", "Think/Nomal/C"),
            Transient("touch.head.normal", AnimationIntent.TouchHeadReflex, "Touch_Head/A_Nomal", "Touch_Head/B_Nomal", "Touch_Head/C_Nomal"),
            Transient("touch.body.happy", AnimationIntent.TouchBodyReflex, "Touch_Body/A_Happy/tb1", "Touch_Body/B_Happy/tb1", "Touch_Body/C_Happy/tb1"),
            Transient("speech.neutral", AnimationIntent.Neutral, "Say/Self/A", "Say/Self/B_1", "Say/Self/C"),
            Transient("speech.happy", AnimationIntent.Happy, "Say/Shining/A", "Say/Shining/B_1", "Say/Shining/C"),
            Transient("speech.alert", AnimationIntent.Alert, "Say/Serious/A", "Say/Serious/B", "Say/Serious/C"),
            Transient("speech.worried", AnimationIntent.Worried, "State/StateONE/A_PoorCondition", "State/StateONE/B_PoorCondition", "State/StateONE/C_PoorCondition"),
            LayeredFeed("feed.eat.normal", "Eat/Nomal/back_lay", "Eat/Nomal/front_lay", EatMotion()),
            LayeredFeed("feed.drink.normal", "Drink/Nomal/back_lay", "Drink/front_lay", DrinkMotion()),
        ];

        private AnimationPlan Baseline(string id, AnimationIntent intent, string? entry, string body, string? exit) =>
            new(id, intent, Phase(entry, AnimationPhaseKind.Entry), Phase(body, AnimationPhaseKind.Body, true)!, Phase(exit, AnimationPhaseKind.Exit), true);

        private AnimationPlan Pending(string id, AnimationIntent intent, string? entry, string body, string? exit) =>
            new(id, intent, Phase(entry, AnimationPhaseKind.Entry), Phase(body, AnimationPhaseKind.Body, true)!, Phase(exit, AnimationPhaseKind.Exit), false, true);

        private AnimationPlan Transient(string id, AnimationIntent intent, string? entry, string body, string? exit) =>
            new(id, intent, Phase(entry, AnimationPhaseKind.Entry), Phase(body, AnimationPhaseKind.Body)!, Phase(exit, AnimationPhaseKind.Exit));

        private AnimationPlan LayeredFeed(
            string id,
            string back,
            string front,
            IReadOnlyList<AnimationMotionFrame> itemMotion)
        {
            var body = new AnimationPhasePlan(
                AnimationPhaseKind.Body,
                false,
                [Layer("back", 0, back), Layer("front", 2, front)]);
            return new AnimationPlan(id, AnimationIntent.Eat, null, body, null, ItemMotion: itemMotion);
        }

        private static IReadOnlyList<AnimationMotionFrame> EatMotion() =>
        [
            Motion(175, 205, 23, 60, opacity: 0.375),
            Motion(125, 220, 88, 60, rotation: 25, opacity: 0.4375),
            Motion(125, 222, 83, 60, rotation: -20),
            Motion(125, 216, 178, 57, rotation: -5.5),
            Motion(750, 212, 196, 65),
            Motion(125, 210, 163, 65),
            Motion(375, 212, 194, 65),
            Motion(125, 210, 158, 65),
            Hidden(750),
        ];

        private static IReadOnlyList<AnimationMotionFrame> DrinkMotion() =>
        [
            Hidden(1000),
            Motion(125, 268, 286, 77, rotation: 60),
            Motion(125, 167, 164, 78),
            Motion(125, 198, 160, 78),
            Motion(125, 199, 163, 78),
            Motion(125, 199, 160, 78),
            Motion(125, 199, 163, 78),
            Motion(125, 199, 160, 78),
            Motion(125, 169, 164, 78),
            Motion(125, 268, 286, 77, rotation: 60),
            Hidden(625),
        ];

        private static AnimationMotionFrame Motion(
            int durationMs,
            double x,
            double y,
            double width,
            double rotation = 0,
            double opacity = 1) =>
            new(
                durationMs,
                new LayerPlacement(
                    width,
                    width,
                    x,
                    y,
                    LayerCoordinateSpace.LogicalCanvas,
                    500),
                rotation,
                opacity);

        private static AnimationMotionFrame Hidden(int durationMs) =>
            new(durationMs, Visible: false);

        private AnimationPhasePlan? Phase(string? relative, AnimationPhaseKind kind, bool loop = false) =>
            relative is null ? null : new AnimationPhasePlan(kind, loop, [Layer("main", 0, relative)]);

        private AnimationLayerPlan Layer(string name, int zIndex, string relative)
        {
            var folder = Path.Combine(_root, relative.Replace('/', Path.DirectorySeparatorChar));
            if (!Directory.Exists(folder)) throw new DirectoryNotFoundException($"动画目录不存在：{relative}");
            var frames = Directory.EnumerateFiles(folder, "*.png", SearchOption.TopDirectoryOnly)
                .OrderBy(path => path, StringComparer.OrdinalIgnoreCase)
                .Select(path => new AnimationFrameSpec(path, ParseDuration(path)))
                .ToArray();
            return new AnimationLayerPlan(name, zIndex, frames);
        }

        private static int ParseDuration(string path)
        {
            var match = DurationPattern().Match(Path.GetFileNameWithoutExtension(path));
            if (!match.Success || !int.TryParse(match.Groups[1].Value, out var duration) || duration <= 0)
            {
                throw new InvalidDataException($"动画帧文件名缺少合法时长：{path}");
            }
            return duration;
        }
    }

    [GeneratedRegex(@"_(\d+)$")]
    private static partial Regex DurationPattern();
}

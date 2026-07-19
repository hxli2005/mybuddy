using System.IO;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace BuddyShell.Anim;

public enum BodyActionShape { Stationary, Horizontal, Interactive }
public enum BodyActionDirection { Still, Left, Right }

public sealed record BodyActionAnimation(
    BodyActionDirection Direction,
    AnimationIntent Intent,
    string PlanId,
    IReadOnlyList<string> Entry,
    string Body,
    string Exit);

public sealed record BodyActionDefinition(
    string Type,
    BodyActionShape Shape,
    IReadOnlyList<BodyActionAnimation> Animations)
{
    public BodyActionAnimation Animation(BodyActionDirection direction) =>
        Animations.Single(animation => animation.Direction == direction);
}

public sealed class BodyActionCatalog
{
    private const string ResourceName = "BuddyShell.Anim.body-actions.json";
    private static readonly JsonSerializerOptions Json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
        UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
        Converters = { new JsonStringEnumConverter(JsonNamingPolicy.SnakeCaseLower, false) },
    };
    private readonly Dictionary<string, BodyActionDefinition> _actions;

    private BodyActionCatalog(IEnumerable<BodyActionDefinition> actions)
    {
        var definitions = actions.ToArray();
        var errors = Validate(definitions);
        if (errors.Count > 0) throw new InvalidDataException(string.Join("\n", errors));
        _actions = definitions.ToDictionary(action => action.Type, StringComparer.Ordinal);
    }

    public static BodyActionCatalog Default { get; } = LoadDefault();
    public IReadOnlyCollection<BodyActionDefinition> Actions => _actions.Values;

    public bool TryGet(string type, out BodyActionDefinition? action) =>
        _actions.TryGetValue(type, out action);

    public BodyActionDefinition Get(string type) => _actions.TryGetValue(type, out var action)
        ? action
        : throw new KeyNotFoundException($"身体动作不存在：{type}");

    public static BodyActionCatalog Parse(string json)
    {
        var actions = JsonSerializer.Deserialize<BodyActionDefinition[]>(json, Json)
            ?? throw new InvalidDataException("身体动作目录必须是 JSON 数组。");
        return new BodyActionCatalog(actions);
    }

    private static BodyActionCatalog LoadDefault()
    {
        using var stream = Assembly.GetExecutingAssembly().GetManifestResourceStream(ResourceName)
            ?? throw new InvalidDataException($"缺少内置身体动作目录：{ResourceName}");
        using var reader = new StreamReader(stream);
        var catalog = Parse(reader.ReadToEnd());
        if (!catalog.TryGet("read", out var read) || read?.Shape != BodyActionShape.Stationary ||
            !catalog.TryGet("walk", out var walk) || walk?.Shape != BodyActionShape.Horizontal)
            throw new InvalidDataException("内置身体动作目录必须包含 stationary read 和 horizontal walk。");
        return catalog;
    }

    private static IReadOnlyList<string> Validate(IReadOnlyList<BodyActionDefinition> actions)
    {
        var errors = new List<string>();
        foreach (var group in actions.GroupBy(action => action.Type, StringComparer.Ordinal)
                     .Where(group => string.IsNullOrWhiteSpace(group.Key) || group.Count() > 1))
            errors.Add($"动作类型为空或重复：{group.Key}");

        foreach (var action in actions)
        {
            var expected = action.Shape is BodyActionShape.Stationary or BodyActionShape.Interactive
                ? new[] { BodyActionDirection.Still }
                : new[] { BodyActionDirection.Left, BodyActionDirection.Right };
            var actual = action.Animations.Select(animation => animation.Direction).ToArray();
            if (actual.Length != expected.Length || expected.Any(direction => !actual.Contains(direction)))
                errors.Add($"{action.Type}: {action.Shape} 动作的动画方向不完整");
            foreach (var animation in action.Animations)
            {
                if (animation.Entry.Count == 0 || animation.Entry.Any(string.IsNullOrWhiteSpace) ||
                    new[] { animation.PlanId, animation.Body, animation.Exit }.Any(string.IsNullOrWhiteSpace))
                    errors.Add($"{action.Type}/{animation.Direction}: 动画目录字段不能为空");
            }
        }

        foreach (var group in actions.SelectMany(action => action.Animations)
                     .GroupBy(animation => animation.PlanId, StringComparer.Ordinal)
                     .Where(group => group.Count() > 1))
            errors.Add($"动画 plan 重复：{group.Key}");
        foreach (var group in actions.SelectMany(action => action.Animations)
                     .GroupBy(animation => animation.Intent)
                     .Where(group => group.Count() > 1))
            errors.Add($"动画 intent 重复：{group.Key}");
        return errors;
    }
}

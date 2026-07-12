using System.Diagnostics;

namespace BuddyShell.Anim;

public interface IAnimationClock
{
    long ElapsedMilliseconds { get; }
}

public sealed class SystemAnimationClock : IAnimationClock
{
    private readonly Stopwatch _stopwatch = Stopwatch.StartNew();
    public long ElapsedMilliseconds => _stopwatch.ElapsedMilliseconds;
}

public sealed class ManualAnimationClock : IAnimationClock
{
    public long ElapsedMilliseconds { get; private set; }
    public void Advance(int milliseconds) => ElapsedMilliseconds += Math.Max(0, milliseconds);
}

public static class AnimationTimeline
{
    public static CompositedFrame Compose(
        long generation,
        string planId,
        AnimationPhasePlan phase,
        long elapsedMilliseconds)
    {
        var layers = phase.Layers
            .OrderBy(layer => layer.ZIndex)
            .Select(layer =>
            {
                var frame = SelectFrame(layer, phase.Loop, elapsedMilliseconds);
                return new RenderLayer(
                    layer.Name,
                    layer.ZIndex,
                    frame.Path,
                    frame.Placement ?? layer.Placement,
                    frame.Rotation,
                    frame.Opacity,
                    frame.Visible);
            })
            .ToArray();
        return new CompositedFrame(generation, planId, phase.Kind, layers);
    }

    public static bool IsComplete(AnimationPhasePlan phase, long elapsedMilliseconds) =>
        !phase.Loop && elapsedMilliseconds >= phase.DurationMs;

    private static AnimationFrameSpec SelectFrame(
        AnimationLayerPlan layer,
        bool loop,
        long elapsedMilliseconds)
    {
        if (layer.Frames.Count == 0) throw new InvalidOperationException($"动画层 {layer.Name} 没有帧。");
        var total = Math.Max(1, layer.DurationMs);
        var local = loop
            ? elapsedMilliseconds % total
            : Math.Min(elapsedMilliseconds, total - 1L);
        long cursor = 0;
        foreach (var frame in layer.Frames)
        {
            cursor += frame.DurationMs;
            if (local < cursor) return frame;
        }
        return layer.Frames[^1];
    }
}

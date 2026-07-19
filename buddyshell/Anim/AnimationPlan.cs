namespace BuddyShell.Anim;

public enum AnimationPhaseKind { Entry, Body, Exit }
public enum AnimationExecutionKind { Baseline, Transient, Pending, Interactive }
public enum AnimationSource { State, Touch, Chat, System, DirectManipulation }
public enum AnimationPriority { Activity = 20, Think = 60, Response = 70, Touch = 90, DirectManipulation = 100 }

public sealed record LayerPlacement(
    double Width,
    double Height,
    double OffsetX = 0,
    double OffsetY = 0,
    LayerCoordinateSpace CoordinateSpace = LayerCoordinateSpace.CenteredDips,
    double CanvasSize = 500);

public enum LayerCoordinateSpace { CenteredDips, LogicalCanvas }

public sealed record AnimationFrameSpec(
    string Path,
    int DurationMs,
    LayerPlacement? Placement = null,
    double Rotation = 0,
    double Opacity = 1,
    bool Visible = true);

public sealed record AnimationLayerPlan(
    string Name,
    int ZIndex,
    IReadOnlyList<AnimationFrameSpec> Frames,
    LayerPlacement? Placement = null)
{
    public int DurationMs => Frames.Sum(frame => frame.DurationMs);
}

public sealed record AnimationPhasePlan(
    AnimationPhaseKind Kind,
    bool Loop,
    IReadOnlyList<AnimationLayerPlan> Layers)
{
    public int DurationMs => Layers.Count == 0 ? 0 : Layers.Max(layer => layer.DurationMs);
    public int FrameCount => Layers.Sum(layer => layer.Frames.Count);
}

public sealed record AnimationPlan(
    string Id,
    AnimationIntent Intent,
    AnimationPhasePlan? Entry,
    AnimationPhasePlan Body,
    AnimationPhasePlan? Exit,
    bool IsBaseline = false,
    bool IsPending = false);

public sealed record RenderLayer(
    string Name,
    int ZIndex,
    string SourcePath,
    LayerPlacement? Placement,
    double Rotation,
    double Opacity,
    bool Visible);

public sealed record CompositedFrame(
    long Generation,
    string PlanId,
    AnimationPhaseKind Phase,
    IReadOnlyList<RenderLayer> Layers)
{
    public string Signature => string.Join(
        "|",
        Layers.OrderBy(layer => layer.ZIndex).Select(layer =>
            $"{layer.Name}:{layer.SourcePath}:{layer.Placement}:{layer.Rotation:F3}:{layer.Opacity:F3}:{layer.Visible}"));
}

public sealed record AnimationRequest(
    AnimationIntent Intent,
    AnimationSource Source,
    string CorrelationId,
    AnimationPriority Priority);

public sealed record AnimationOutcome(
    AnimationIntent? Reaction = null,
    bool IsError = false,
    string? Reason = null);

public sealed record AnimationSnapshot(
    long Generation,
    string? PlanId,
    AnimationPhaseKind? Phase,
    AnimationExecutionKind? Execution,
    string? CorrelationId,
    string BaselinePlanId,
    bool ThinkPending,
    int CurrentFrameCount,
    bool IsPlaying);

namespace BuddyShell.Anim;

public enum AnimationIntent
{
    Idle,
    Read,
    Write,
    Gaze,
    Sleep,
    Think,
    TouchHeadReflex,
    TouchBodyReflex,
    Neutral,
    Happy,
}

public enum TouchZone { Head, Body }

public sealed class TouchDetectedEventArgs(TouchZone zone, string? correlationId = null) : EventArgs
{
    public TouchZone Zone { get; } = zone;
    public string CorrelationId { get; } = correlationId ?? Guid.NewGuid().ToString("N");
}

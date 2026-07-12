namespace BuddyShell.Anim;

public enum AnimationIntent
{
    Idle,
    Read,
    Write,
    Nap,
    Gaze,
    Stretch,
    Work,
    Sleep,
    TouchHeadReflex,
    TouchBodyReflex,
    Eat,
    Think,
    Happy,
    Sad,
    Worried,
    Alert,
    Neutral,
}

public enum TouchZone
{
    Head,
    Body,
}

public sealed class TouchDetectedEventArgs(TouchZone zone, string? correlationId = null) : EventArgs
{
    public TouchZone Zone { get; } = zone;
    public string CorrelationId { get; } = correlationId ?? Guid.NewGuid().ToString("N");
}

public sealed record PhysioLevels(bool Hungry, bool Tired, bool Low, bool Bright);

public static class ActionMapper
{
    public static AnimationIntent? TryFrom(string? action, string? expression)
    {
        var actionValue = (action ?? "").Trim().ToLowerInvariant();
        var mappedAction = actionValue switch
        {
            "comfort" or "concern" => AnimationIntent.Worried,
            "greet" or "happy" => AnimationIntent.Happy,
            "remind" or "safety" => AnimationIntent.Alert,
            "thinking" => AnimationIntent.Think,
            "react" or "talk" or "notify" => AnimationIntent.Neutral,
            _ => (AnimationIntent?)null,
        };
        if (mappedAction is not null) return mappedAction;

        return (expression ?? "").Trim().ToLowerInvariant() switch
        {
            "happy" or "smile" => AnimationIntent.Happy,
            "sad" => AnimationIntent.Sad,
            "worried" => AnimationIntent.Worried,
            "alert" => AnimationIntent.Alert,
            "neutral" => AnimationIntent.Neutral,
            _ => null,
        };
    }

    public static AnimationIntent From(string? action, string? expression, string? idleHint = null)
    {
        var mappedAction = TryFrom(action, expression);
        if (mappedAction is not null) return mappedAction.Value;
        var value = (idleHint ?? expression ?? action ?? "idle").Trim().ToLowerInvariant();
        return value switch
        {
            "read" => AnimationIntent.Read,
            "write" => AnimationIntent.Write,
            "nap" => AnimationIntent.Nap,
            "gaze" => AnimationIntent.Gaze,
            "stretch" => AnimationIntent.Stretch,
            "work" => AnimationIntent.Work,
            "sleep" => AnimationIntent.Sleep,
            "eat" => AnimationIntent.Eat,
            "think" or "thinking" => AnimationIntent.Think,
            "happy" or "smile" => AnimationIntent.Happy,
            "sad" => AnimationIntent.Sad,
            "worried" => AnimationIntent.Worried,
            "alert" => AnimationIntent.Alert,
            "neutral" => AnimationIntent.Neutral,
            _ => AnimationIntent.Idle,
        };
    }
}

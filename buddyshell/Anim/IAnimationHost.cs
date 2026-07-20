using System.Windows;

namespace BuddyShell.Anim;

public interface ITouchSource
{
    event EventHandler<TouchDetectedEventArgs>? TouchDetected;
}

public interface IAnimationRenderer : IDisposable, ITouchSource
{
    UIElement View { get; }
    event EventHandler<TouchDetectedEventArgs>? TouchStarted;
    void Render(CompositedFrame frame);
}

public interface IAnimationController : IDisposable, ITouchSource
{
    UIElement View { get; }
    AnimationSnapshot Snapshot { get; }
    event EventHandler<AnimationFaultEventArgs>? Faulted;
    event EventHandler<ActivityFinishedEventArgs>? ActivityFinished;
    void Submit(AnimationRequest request);
    void BeginInteractive(AnimationRequest request, bool resumeBody = false);
    void EndInteractive(
        string correlationId,
        AnimationRequest? followUp = null,
        bool followUpResumeBody = false);
    void Complete(string correlationId, AnimationOutcome outcome);
}

public sealed class ActivityFinishedEventArgs(string activityId, string status, string reason) : EventArgs
{
    public string ActivityId { get; } = activityId;
    public string Status { get; } = status;
    public string Reason { get; } = reason;
    public bool Completed => Status == "completed";
}

public sealed class AnimationFaultEventArgs(Exception? exception, bool recovered) : EventArgs
{
    public Exception? Exception { get; } = exception;
    public bool Recovered { get; } = recovered;
}

public interface IAnimationDiagnostics
{
    string HostName { get; }
    string AssetRoot { get; }
    int CurrentFrameCount { get; }
    bool IsPlaying { get; }
    TouchZone ClassifyTouch(double y, double height);
}

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
    void SetWarmth(double warmth);
}

public interface IAnimationController : IDisposable, ITouchSource
{
    UIElement View { get; }
    AnimationSnapshot Snapshot { get; }
    event EventHandler<AnimationFaultEventArgs>? Faulted;
    void UpdateBaseline(BaselineSnapshot snapshot);
    void Submit(AnimationRequest request);
    void Complete(string correlationId, AnimationOutcome outcome);
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

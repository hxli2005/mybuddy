using System.Windows;

namespace BuddyShell.Anim;

public interface IAnimationHost : IDisposable
{
    UIElement View { get; }

    event EventHandler<TouchDetectedEventArgs>? TouchDetected;

    void Play(AnimationIntent intent, bool loop = false);

    void SetBaseline(PhysioLevels levels, double warmth);
}

public interface IAnimationHostDiagnostics
{
    string HostName { get; }
    string AssetRoot { get; }
    int CurrentFrameCount { get; }
    bool IsPlaying { get; }
    TouchZone ClassifyTouch(double y, double height);
}

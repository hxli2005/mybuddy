using BuddyShell.Anim;
using BuddyShell.Bridge;
using System.Windows;

namespace BuddyShell;

public sealed class WalkAttempt
{
    private const double PixelsPerMillisecond = 0.08;
    private readonly double _direction;
    private long _lastElapsed;

    public WalkAttempt(
        string activityId,
        double left,
        double top,
        double width,
        double height,
        Rect workArea)
    {
        if (string.IsNullOrWhiteSpace(activityId) ||
            !double.IsFinite(left) || !double.IsFinite(top) ||
            !double.IsFinite(width) || !double.IsFinite(height) ||
            width <= 0 || height <= 0 ||
            workArea.Width < width || workArea.Height < height)
            throw new InvalidOperationException("窗口和工作区不足以开始 walk。");

        ActivityId = activityId;
        WorkArea = workArea;
        WindowWidth = width;
        WindowHeight = height;
        MaxLeft = workArea.Right - width;
        MaxTop = workArea.Bottom - height;
        StartLeft = Math.Clamp(left, workArea.Left, MaxLeft);
        StartTop = Math.Clamp(top, workArea.Top, MaxTop);
        Left = StartLeft;
        Top = StartTop;

        var leftRoom = StartLeft - workArea.Left;
        var rightRoom = MaxLeft - StartLeft;
        if (Math.Max(leftRoom, rightRoom) < 1)
            throw new InvalidOperationException("工作区没有可核验的水平 walk 距离。");
        _direction = rightRoom >= leftRoom ? 1 : -1;
        Intent = _direction > 0 ? AnimationIntent.WalkRight : AnimationIntent.WalkLeft;
    }

    public string ActivityId { get; }
    public AnimationIntent Intent { get; }
    public Rect WorkArea { get; }
    public double WindowWidth { get; }
    public double WindowHeight { get; }
    public double MaxLeft { get; }
    public double MaxTop { get; }
    public double StartLeft { get; }
    public double StartTop { get; }
    public double Left { get; private set; }
    public double Top { get; private set; }

    public void Advance(long elapsedMilliseconds)
    {
        var elapsed = Math.Max(_lastElapsed, elapsedMilliseconds);
        var distance = (elapsed - _lastElapsed) * PixelsPerMillisecond;
        _lastElapsed = elapsed;
        Left = Math.Clamp(Left + _direction * distance, WorkArea.Left, MaxLeft);
    }

    public bool Contains(double left, double top) =>
        double.IsFinite(left) && double.IsFinite(top) &&
        left >= WorkArea.Left - 0.5 && left <= MaxLeft + 0.5 &&
        top >= WorkArea.Top - 0.5 && top <= MaxTop + 0.5;

    public BodyWalkMotion Capture(double endLeft, double endTop) => new()
    {
        StartLeft = StartLeft,
        StartTop = StartTop,
        EndLeft = endLeft,
        EndTop = endTop,
        WindowWidth = WindowWidth,
        WindowHeight = WindowHeight,
        WorkLeft = WorkArea.Left,
        WorkTop = WorkArea.Top,
        WorkRight = WorkArea.Right,
        WorkBottom = WorkArea.Bottom,
    };
}

using System.Windows;

namespace BuddyShell;

public enum EdgeSide { Left, Right }

public static class EdgeDock
{
    public const double VisibleWidth = 144;
    public const double SnapDistance = 28;

    public static EdgeSide? Detect(Rect workArea, double left, double width)
    {
        if (left <= workArea.Left + SnapDistance) return EdgeSide.Left;
        if (left + width >= workArea.Right - SnapDistance) return EdgeSide.Right;
        return null;
    }

    public static Point Place(EdgeSide side, Rect workArea, double width, double height, double top)
    {
        var left = side == EdgeSide.Left
            ? workArea.Left - width + VisibleWidth
            : workArea.Right - VisibleWidth;
        var clampedTop = Math.Clamp(top, workArea.Top, Math.Max(workArea.Top, workArea.Bottom - height));
        return new Point(left, clampedTop);
    }

    public static double TopRatio(Rect workArea, double height, double top)
    {
        var travel = Math.Max(0, workArea.Height - height);
        return travel <= 0 ? 0 : Math.Clamp((top - workArea.Top) / travel, 0, 1);
    }

    public static double TopFromRatio(Rect workArea, double height, double ratio) =>
        workArea.Top + Math.Max(0, workArea.Height - height) * Math.Clamp(ratio, 0, 1);
}

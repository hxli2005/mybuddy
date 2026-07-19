using BuddyShell.Bridge;
using System.Runtime.InteropServices;

namespace BuddyShell;

public sealed class Presence(ShellSettings settings)
{
    public BodyPresence Snapshot(bool edgeDocked = false)
    {
        var fullscreen = ForegroundIsFullscreen();
        return new BodyPresence
        {
            Present = GetIdleSeconds() < Math.Max(1, settings.IdlePauseMinutes) * 60,
            Fullscreen = fullscreen,
            Surface = edgeDocked ? "edge" : "full",
        };
    }

    private static uint GetIdleSeconds()
    {
        var info = new LastInputInfo { Size = (uint)Marshal.SizeOf<LastInputInfo>() };
        return GetLastInputInfo(ref info)
            ? unchecked((uint)Environment.TickCount - info.Time) / 1000
            : 0;
    }

    private static bool ForegroundIsFullscreen()
    {
        var handle = GetForegroundWindow();
        if (handle == IntPtr.Zero || !GetWindowRect(handle, out var rect)) return false;
        var screen = System.Windows.Forms.Screen.FromHandle(handle).Bounds;
        return rect.Left <= screen.Left && rect.Top <= screen.Top &&
               rect.Right >= screen.Right && rect.Bottom >= screen.Bottom;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct LastInputInfo { public uint Size; public uint Time; }

    [StructLayout(LayoutKind.Sequential)]
    private struct Rect { public int Left; public int Top; public int Right; public int Bottom; }

    [DllImport("user32.dll")]
    private static extern bool GetLastInputInfo(ref LastInputInfo info);

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr handle, out Rect rect);
}

using BuddyShell.Bridge;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Windows.Threading;

namespace BuddyShell;

public sealed class Presence : IDisposable
{
    private readonly BridgeClient _client;
    private readonly Outbox _outbox;
    private readonly ShellSettings _settings;
    private readonly Func<string?> _workSessionId;
    private readonly Func<bool> _sleeping;
    private readonly Func<bool> _stateAvailable;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(5) };
    private double _presentSeconds;
    private double _awaySeconds;
    private bool _awayQualified;
    private long _lastTick = Stopwatch.GetTimestamp();
    private bool? _wasPresent;

    public Presence(
        BridgeClient client,
        Outbox outbox,
        ShellSettings settings,
        Func<string?> workSessionId,
        Func<bool> sleeping,
        Func<bool> stateAvailable)
    {
        _client = client;
        _outbox = outbox;
        _settings = settings;
        _workSessionId = workSessionId;
        _sleeping = sleeping;
        _stateAvailable = stateAvailable;
        _timer.Tick += OnTick;
    }

    public event EventHandler? UserReturned;
    public bool IsPresent { get; private set; } = true;
    public bool IsFullscreen => ForegroundIsFullscreen();
    public uint IdleSeconds => GetIdleSeconds();

    public void Start()
    {
        _lastTick = Stopwatch.GetTimestamp();
        _timer.Start();
    }

    private async void OnTick(object? sender, EventArgs e)
    {
        try
        {
            var elapsed = Stopwatch.GetElapsedTime(_lastTick);
            _lastTick = Stopwatch.GetTimestamp();
            if (!_stateAvailable())
            {
                IsPresent = false;
                return;
            }
            var idleSeconds = GetIdleSeconds();
            var awayThresholdSeconds = Math.Max(1, _settings.IdlePauseMinutes) * 60;
            IsPresent = idleSeconds < awayThresholdSeconds &&
                !ForegroundIsFullscreen() && !_sleeping();
            if (IsPresent && _wasPresent == false && _awayQualified)
            {
                UserReturned?.Invoke(this, EventArgs.Empty);
            }
            _wasPresent = IsPresent;
            if (!IsPresent)
            {
                _presentSeconds = 0;
                _awaySeconds += Math.Min(elapsed.TotalSeconds, 60);
                _awayQualified |= idleSeconds >= awayThresholdSeconds ||
                    _awaySeconds >= awayThresholdSeconds;
                return;
            }
            _awaySeconds = 0;
            _awayQualified = false;
            _presentSeconds += Math.Min(elapsed.TotalSeconds, 60);
            if (_presentSeconds < TimeSpan.FromMinutes(20).TotalSeconds) return;
            var coveredMinutes = Math.Min(20, (int)(_presentSeconds / 60));
            var request = new VPetEventRequest
            {
                Event = "presence_heartbeat",
                Count = coveredMinutes,
                Context = new()
                {
                    ["idle_seconds"] = GetIdleSeconds(),
                    ["fullscreen"] = false,
                    ["work_session_id"] = _workSessionId(),
                },
            };
            try
            {
                await _client.SendEventAsync(request);
                App.LogMessage(
                    $"event=presence_heartbeat client_event_id={request.ClientEventId} " +
                    $"count={request.Count}");
                _presentSeconds -= coveredMinutes * 60;
            }
            catch (BridgeRequestException)
            {
                await _outbox.EnqueueAsync(request);
                _presentSeconds -= coveredMinutes * 60;
            }
        }
        catch (Exception exception)
        {
            App.LogException(exception);
        }
    }

    public void Dispose() => _timer.Stop();

    private static uint GetIdleSeconds()
    {
        var info = new LastInputInfo { Size = (uint)Marshal.SizeOf<LastInputInfo>() };
        return GetLastInputInfo(ref info) ? unchecked((uint)Environment.TickCount - info.Time) / 1000 : 0;
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
    [DllImport("user32.dll")] private static extern bool GetLastInputInfo(ref LastInputInfo info);
    [DllImport("user32.dll")] private static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] private static extern bool GetWindowRect(IntPtr handle, out Rect rect);
}

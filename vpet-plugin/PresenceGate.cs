using System;
using System.Runtime.InteropServices;
using System.Threading;
using System.Threading.Tasks;

namespace MyBuddy.VPetPlugin;

public sealed class PresenceGate
{
    private readonly Func<VPetEventRequest, CancellationToken, Task<VPetEventResponse>> _postEvent;
    private readonly Func<CancellationToken, Task<VPetPendingResponse>> _drainDigest;
    private readonly Func<BodyState?> _captureBodyState;
    private readonly BridgeSettings _settings;
    private bool _wasAway;

    public PresenceGate(
        BridgeSettings settings,
        Func<VPetEventRequest, CancellationToken, Task<VPetEventResponse>> postEvent,
        Func<CancellationToken, Task<VPetPendingResponse>> drainDigest,
        Func<BodyState?> captureBodyState)
    {
        _settings = settings;
        _postEvent = postEvent;
        _drainDigest = drainDigest;
        _captureBodyState = captureBodyState;
    }

    public bool IsDrainPaused => GetIdleTime() > TimeSpan.FromMinutes(_settings.IdlePauseMinutes);

    public async Task<VPetPendingResponse?> PollAsync(CancellationToken cancellationToken = default)
    {
        var isAway = IsDrainPaused;
        if (isAway)
        {
            _wasAway = true;
            return null;
        }

        if (!_wasAway)
        {
            return null;
        }

        _wasAway = false;
        await _postEvent(
            new VPetEventRequest
            {
                Event = "user_back",
                Count = 1,
                WantReply = false,
                BodyState = _captureBodyState(),
                ClientEventId = $"vpet-user-back-{DateTimeOffset.Now:yyyyMMddHHmmss}-{Guid.NewGuid():N}",
            },
            cancellationToken).ConfigureAwait(false);
        return await _drainDigest(cancellationToken).ConfigureAwait(false);
    }

    public TimeSpan GetIdleTime()
    {
        var lastInput = new LASTINPUTINFO();
        lastInput.cbSize = (uint)Marshal.SizeOf(lastInput);
        if (!GetLastInputInfo(ref lastInput))
        {
            return TimeSpan.Zero;
        }

        var tickNow = GetTickCount64();
        var idleMs = tickNow >= lastInput.dwTime ? tickNow - lastInput.dwTime : 0;
        return TimeSpan.FromMilliseconds(idleMs);
    }

    [DllImport("user32.dll")]
    private static extern bool GetLastInputInfo(ref LASTINPUTINFO plii);

    [DllImport("kernel32.dll")]
    private static extern ulong GetTickCount64();

    [StructLayout(LayoutKind.Sequential)]
    private struct LASTINPUTINFO
    {
        public uint cbSize;
        public uint dwTime;
    }
}

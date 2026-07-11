using BuddyShell.Bridge;
using System.Globalization;
using System.Windows.Threading;

namespace BuddyShell;

public sealed class Notices : IDisposable
{
    private readonly BridgeClient _client;
    private readonly Outbox _outbox;
    private readonly Bubble _bubble;
    private readonly ShellSettings _settings;
    private readonly Func<VPetStateResponse?> _state;
    private readonly Func<bool> _fullscreen;
    private readonly Action _bringToForeground;
    private readonly DispatcherTimer _timer = new() { Interval = TimeSpan.FromSeconds(30) };
    private DateTimeOffset? _lastPhysical;
    private string? _physicalDate;
    private int _physicalCount;
    private bool _draining;

    public Notices(
        BridgeClient client,
        Outbox outbox,
        Bubble bubble,
        ShellSettings settings,
        Func<VPetStateResponse?> state,
        Func<bool> fullscreen,
        Action bringToForeground)
    {
        _client = client;
        _outbox = outbox;
        _bubble = bubble;
        _settings = settings;
        _state = state;
        _fullscreen = fullscreen;
        _bringToForeground = bringToForeground;
        _physicalDate = settings.PhysicalDate;
        _physicalCount = Math.Max(0, settings.PhysicalCount);
        if (DateTimeOffset.TryParse(settings.LastPhysicalServerTime, out var lastPhysical))
        {
            _lastPhysical = lastPhysical;
        }
        _timer.Tick += async (_, _) => await DrainAsync(digest: false);
    }

    public void Start() => _timer.Start();

    public async Task<bool> DrainAsync(bool digest = false)
    {
        if (_draining || !CanDisplay()) return false;
        _draining = true;
        var displayed = false;
        try
        {
            var response = await _client.DrainAsync(digest);
            if (_settings.PhysicalProactive != response.ServerFlags.PhysicalProactive)
            {
                _settings.PhysicalProactive = response.ServerFlags.PhysicalProactive;
                SettingsStore.Save(_settings);
            }
            if (response.Digest is { Text.Length: > 0 } digestContent)
            {
                _bubble.ShowSpeech(digestContent.Text, interrupt: false);
                await AcknowledgeAsync(null, "digest", false, false);
                displayed = true;
            }
            displayed |= await DisplayPendingAsync(response.Events);
        }
        catch (BridgeRequestException)
        {
            // drain 不能重放；下轮重新拉取。
        }
        catch (Exception exception)
        {
            App.LogException(exception);
        }
        finally
        {
            _draining = false;
        }
        return displayed;
    }

    public async Task<bool> DisplayPendingAsync(IEnumerable<VPetPendingEvent> events)
    {
        var displayed = false;
        try
        {
            foreach (var pending in events)
            {
                if (!CanDisplay()) break;
                var speech = pending.Speech;
                var text = speech?.Text ?? pending.Text;
                if (string.IsNullOrWhiteSpace(text)) continue;
                var persistent = speech?.Persistent == true;
                var effectiveInterrupt = speech?.Interrupt == true && CanPhysicallyInterrupt();
                if (persistent)
                {
                    _bubble.ShowPersistent(text);
                }
                else
                {
                    if (effectiveInterrupt) _bringToForeground();
                    _bubble.ShowSpeech(text, effectiveInterrupt);
                    if (effectiveInterrupt) RecordPhysicalDisplay();
                }
                await AcknowledgeAsync(
                    pending.Id,
                    pending.Source,
                    effectiveInterrupt,
                    persistent);
                displayed = true;
            }
        }
        catch (Exception exception)
        {
            App.LogException(exception);
        }
        return displayed;
    }

    public Task AcknowledgeExternalAsync(string source, bool interrupt = false) =>
        AcknowledgeAsync(null, source, interrupt, persistent: false);

    private bool CanDisplay()
    {
        var state = _state();
        if (state?.Physio?.Sleeping == true || _fullscreen()) return false;
        if (state is null || !TryServerTime(state, out var serverTime)) return false;
        _settings.NormalizeTodayQuiet(serverTime.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture));
        return !_settings.TodayQuiet;
    }

    private bool CanPhysicallyInterrupt()
    {
        if (!_settings.PhysicalProactive) return false;
        var state = _state();
        if (state is null || !TryServerTime(state, out var serverTime)) return false;
        var date = serverTime.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
        if (!string.Equals(date, _physicalDate, StringComparison.Ordinal))
        {
            _physicalDate = date;
            _physicalCount = 0;
            _settings.PhysicalDate = date;
            _settings.PhysicalCount = 0;
            _settings.LastPhysicalServerTime = null;
            SettingsStore.Save(_settings);
        }
        return _physicalCount < Math.Max(0, _settings.PhysicalDailyLimit) &&
               (_lastPhysical is null || serverTime - _lastPhysical >=
                   TimeSpan.FromMinutes(Math.Max(1, _settings.PhysicalCooldownMinutes)));
    }

    private void RecordPhysicalDisplay()
    {
        var state = _state();
        if (state is null || !TryServerTime(state, out var serverTime)) return;
        _lastPhysical = serverTime;
        _physicalCount += 1;
        _settings.PhysicalDate = _physicalDate;
        _settings.PhysicalCount = _physicalCount;
        _settings.LastPhysicalServerTime = serverTime.ToString("o", CultureInfo.InvariantCulture);
        SettingsStore.Save(_settings);
    }

    private async Task AcknowledgeAsync(int? id, string source, bool interrupt, bool persistent)
    {
        var state = _state();
        if (state is null || !TryServerTime(state, out var shownAt)) return;
        var request = new VPetEventRequest
        {
            Event = "notice_shown",
            Context = new()
            {
                ["pending_id"] = id,
                ["source"] = source,
                ["interrupt"] = interrupt,
                ["persistent"] = persistent,
                ["shown_at"] = shownAt.ToString("o", CultureInfo.InvariantCulture),
            },
        };
        try
        {
            await _client.SendEventAsync(request);
            App.LogMessage(
                $"event=notice_shown server_time={shownAt:o} client_event_id={request.ClientEventId} " +
                $"pending_id={id} source={source}");
        }
        catch (BridgeRequestException) { await _outbox.EnqueueAsync(request); }
    }

    private static bool TryServerTime(VPetStateResponse state, out DateTimeOffset value) =>
        DateTimeOffset.TryParse(
            state.ServerTime,
            CultureInfo.InvariantCulture,
            DateTimeStyles.RoundtripKind,
            out value);

    public void Dispose() => _timer.Stop();
}

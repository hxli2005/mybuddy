using BuddyShell.Anim;
using BuddyShell.Bridge;

namespace BuddyShell;

public sealed class TouchLayer : IDisposable
{
    private readonly BridgeClient _client;
    private readonly Outbox _outbox;
    private readonly Func<string?> _serverDate;
    private readonly Dictionary<TouchZone, TouchWindow> _windows = [];
    private string? _firstDate;

    public TouchLayer(
        IAnimationHost host,
        BridgeClient client,
        Outbox outbox,
        Func<string?> serverDate)
    {
        _client = client;
        _outbox = outbox;
        _serverDate = serverDate;
        host.TouchDetected += OnTouchDetected;
        Host = host;
    }

    private IAnimationHost Host { get; }
    public event EventHandler<VPetEventResponse>? ResponseReceived;

    private async void OnTouchDetected(object? sender, TouchDetectedEventArgs args)
    {
        try
        {
            var now = DateTimeOffset.UtcNow;
            if (!_windows.TryGetValue(args.Zone, out var window) ||
                now - window.StartedAt >= TimeSpan.FromSeconds(30))
            {
                window = new TouchWindow(now);
                _windows[args.Zone] = window;
            }
            if (now - window.LastAcceptedAt < TimeSpan.FromMilliseconds(120)) return;
            window.LastAcceptedAt = now;
            window.Count += 1;
            var date = _serverDate();
            var firstToday = date is not null && !string.Equals(_firstDate, date, StringComparison.Ordinal);
            if (firstToday) _firstDate = date;
            var thresholdCrossed = window.Count >= 5 && !window.ThresholdRequested;
            if (thresholdCrossed) window.ThresholdRequested = true;
            var request = new VPetEventRequest
            {
                Event = args.Zone == TouchZone.Head ? "touch_head" : "touch_body",
                Count = 1,
                WantReply = firstToday || thresholdCrossed,
                Context = new()
                {
                    ["zone"] = args.Zone.ToString().ToLowerInvariant(),
                    ["window_count"] = window.Count,
                },
            };
            try
            {
                var response = await _client.SendEventAsync(request);
                App.LogMessage(
                    $"event={request.Event} client_event_id={request.ClientEventId} " +
                    $"event_log_id={response.EventLogId} replied={response.Replied}");
                ResponseReceived?.Invoke(this, response);
            }
            catch (BridgeRequestException)
            {
                await _outbox.EnqueueAsync(request);
            }
        }
        catch (Exception exception)
        {
            App.LogException(exception);
        }
    }

    public void Dispose() => Host.TouchDetected -= OnTouchDetected;

    private sealed class TouchWindow(DateTimeOffset startedAt)
    {
        public DateTimeOffset StartedAt { get; } = startedAt;
        public DateTimeOffset LastAcceptedAt { get; set; } = startedAt - TimeSpan.FromSeconds(1);
        public int Count { get; set; }
        public bool ThresholdRequested { get; set; }
    }
}

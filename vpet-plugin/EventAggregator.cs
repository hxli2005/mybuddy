using System;
using System.Collections.Generic;
using System.Threading;
using System.Threading.Tasks;

namespace MyBuddy.VPetPlugin;

public sealed class EventAggregator
{
    private static readonly TimeSpan WindowSize = TimeSpan.FromSeconds(30);
    private static readonly TimeSpan MinRepeatGap = TimeSpan.FromMilliseconds(120);
    private static readonly TimeSpan LongPressCutoff = TimeSpan.FromMilliseconds(1200);

    private readonly Func<VPetEventRequest, CancellationToken, Task<VPetEventResponse>> _postEvent;
    private readonly Func<BodyState?> _captureBodyState;
    private readonly object _gate = new();
    private readonly Dictionary<string, EventWindow> _windows = new();
    private DateTimeOffset _today = DateTimeOffset.Now.Date;
    private bool _sentFirstTouchToday;

    public EventAggregator(
        Func<VPetEventRequest, CancellationToken, Task<VPetEventResponse>> postEvent,
        Func<BodyState?> captureBodyState)
    {
        _postEvent = postEvent;
        _captureBodyState = captureBodyState;
    }

    public Task<VPetEventResponse?> RecordTouchAsync(
        string eventName,
        TouchEventArgs e,
        CancellationToken cancellationToken = default)
    {
        // 行为约束:反射层不拦截。这里只额外异步 POST event,不阻止 VPet 原生触摸动画/台词。
        if (IsLikelyDragOrRepeat(eventName, e))
        {
            return Task.FromResult<VPetEventResponse?>(null);
        }

        VPetEventRequest? request = null;
        lock (_gate)
        {
            ResetDailyIfNeeded(e.OccurredAt);
            var window = GetWindow(eventName, e.OccurredAt);
            window.Count += 1;
            window.LastAcceptedAt = e.OccurredAt;

            var wantsReply = (!_sentFirstTouchToday || window.Count >= 5) && !window.ReplyRequested;
            if (wantsReply)
            {
                _sentFirstTouchToday = true;
                window.ReplyRequested = true;
                request = BuildRequest(window, eventName, wantReply: true);
            }
        }

        return request == null
            ? Task.FromResult<VPetEventResponse?>(null)
            : SendMaybeReplyAsync(request, cancellationToken);
    }

    public Task<VPetEventResponse?> RecordFeedAsync(
        string? itemName,
        CancellationToken cancellationToken = default)
    {
        var now = DateTimeOffset.Now;
        lock (_gate)
        {
            var window = GetWindow("feed", now);
            window.Count += 1;
            window.LastAcceptedAt = now;
            window.Context["item"] = string.IsNullOrWhiteSpace(itemName) ? "一点东西" : itemName;
        }
        return Task.FromResult<VPetEventResponse?>(null);
    }

    public async Task<IReadOnlyList<VPetEventResponse>> FlushExpiredAsync(
        CancellationToken cancellationToken = default)
    {
        var now = DateTimeOffset.Now;
        var requests = new List<VPetEventRequest>();
        lock (_gate)
        {
            foreach (var pair in _windows)
            {
                var window = pair.Value;
                if (window.Count == 0 || now - window.StartedAt < WindowSize)
                {
                    continue;
                }
                if (!window.TelemetrySent)
                {
                    requests.Add(BuildRequest(window, pair.Key, wantReply: false));
                    window.TelemetrySent = true;
                }
            }
            foreach (var key in new List<string>(_windows.Keys))
            {
                if (now - _windows[key].StartedAt >= WindowSize)
                {
                    _windows.Remove(key);
                }
            }
        }

        var results = new List<VPetEventResponse>();
        foreach (var request in requests)
        {
            results.Add(await _postEvent(request, cancellationToken).ConfigureAwait(false));
        }
        return results;
    }

    private async Task<VPetEventResponse?> SendMaybeReplyAsync(
        VPetEventRequest request,
        CancellationToken cancellationToken)
    {
        return await _postEvent(request, cancellationToken).ConfigureAwait(false);
    }

    private bool IsLikelyDragOrRepeat(string eventName, TouchEventArgs e)
    {
        if (e.PointerMoved || e.PressDuration > LongPressCutoff)
        {
            return true;
        }
        lock (_gate)
        {
            if (!_windows.TryGetValue(eventName, out var window))
            {
                return false;
            }
            return e.OccurredAt - window.LastAcceptedAt < MinRepeatGap;
        }
    }

    private EventWindow GetWindow(string eventName, DateTimeOffset now)
    {
        if (!_windows.TryGetValue(eventName, out var window) || now - window.StartedAt >= WindowSize)
        {
            window = new EventWindow(now);
            _windows[eventName] = window;
        }
        return window;
    }

    private void ResetDailyIfNeeded(DateTimeOffset now)
    {
        if (now.Date == _today.Date)
        {
            return;
        }
        _today = now.Date;
        _sentFirstTouchToday = false;
    }

    private VPetEventRequest BuildRequest(EventWindow window, string eventName, bool wantReply)
    {
        return new VPetEventRequest
        {
            Event = eventName,
            Count = Math.Max(1, window.Count),
            BodyState = _captureBodyState(),
            Context = window.Context.Count == 0 ? null : new Dictionary<string, object?>(window.Context),
            WantReply = wantReply,
            ClientEventId = window.ClientEventId,
        };
    }

    private sealed class EventWindow
    {
        public EventWindow(DateTimeOffset startedAt)
        {
            StartedAt = startedAt;
            LastAcceptedAt = startedAt - TimeSpan.FromSeconds(1);
            ClientEventId = $"vpet-{startedAt:yyyyMMddHHmmss}-{Guid.NewGuid():N}";
        }

        public DateTimeOffset StartedAt { get; }
        public DateTimeOffset LastAcceptedAt { get; set; }
        public string ClientEventId { get; }
        public int Count { get; set; }
        public bool ReplyRequested { get; set; }
        public bool TelemetrySent { get; set; }
        public Dictionary<string, object?> Context { get; } = new();
    }
}

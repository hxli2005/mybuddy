using System;
using System.Threading;
using System.Threading.Tasks;

namespace MyBuddy.VPetPlugin;

public sealed class MyBuddyPlugin : IDisposable
{
    private readonly IVPetHost _host;
    private readonly BridgeSettings _settings;
    private readonly BridgeClient _client;
    private readonly ActionMapper _mapper;
    private readonly EventAggregator _events;
    private readonly PresenceGate _presence;
    private readonly DrainWorker _drain;
    private readonly CancellationTokenSource _shutdown = new();
    private Timer? _presenceTimer;
    private Timer? _drainTimer;
    private Timer? _eventFlushTimer;
    private bool _disposed;

    public MyBuddyPlugin(IVPetHost host, BridgeSettings settings)
    {
        _host = host;
        _settings = settings;
        _client = new BridgeClient(settings);
        _mapper = new ActionMapper();
        _events = new EventAggregator(PostEventAndMaybeShowAsync, _host.CaptureBodyState);
        _presence = new PresenceGate(
            settings,
            PostEventAndMaybeShowAsync,
            _client.DrainDigestAsync,
            _host.CaptureBodyState);
        _drain = new DrainWorker(settings, _client, host, _mapper);
    }

    public void Start()
    {
        _host.TouchHead += OnTouchHead;
        _host.TouchBody += OnTouchBody;
        _host.Feed += OnFeed;
        _host.ChatSubmitted += OnChatSubmitted;
        _host.SettingsRequested += OnSettingsRequested;

        _presenceTimer = new Timer(
            _ => RunFireAndForget(PollPresenceAsync),
            null,
            TimeSpan.FromSeconds(1),
            TimeSpan.FromSeconds(Math.Max(1, _settings.PresencePollSeconds)));
        _drainTimer = new Timer(
            _ => RunFireAndForget(PollDrainAsync),
            null,
            TimeSpan.FromSeconds(3),
            TimeSpan.FromSeconds(Math.Max(10, _settings.DrainPollSeconds)));
        _eventFlushTimer = new Timer(
            _ => RunFireAndForget(FlushEventsAsync),
            null,
            TimeSpan.FromSeconds(5),
            TimeSpan.FromSeconds(5));

        _host.ShowBridgeStatus("MyBuddy bridge ready.", BridgeStatusKind.Normal);
    }

    public void Stop()
    {
        _host.TouchHead -= OnTouchHead;
        _host.TouchBody -= OnTouchBody;
        _host.Feed -= OnFeed;
        _host.ChatSubmitted -= OnChatSubmitted;
        _host.SettingsRequested -= OnSettingsRequested;
        _presenceTimer?.Dispose();
        _drainTimer?.Dispose();
        _eventFlushTimer?.Dispose();
    }

    public void UpdateSettings(BridgeSettings settings)
    {
        _settings.BridgeUrl = settings.BridgeUrl;
        _settings.BridgeToken = settings.BridgeToken;
        _settings.BodyStateInjection = settings.BodyStateInjection;
        _settings.TouchEscalation = settings.TouchEscalation;
        _settings.PhysicalProactive = settings.PhysicalProactive;
        _settings.TodayQuiet = settings.TodayQuiet;
        _settings.IdlePauseMinutes = settings.IdlePauseMinutes;
        _settings.DrainPollSeconds = settings.DrainPollSeconds;
        _settings.PresencePollSeconds = settings.PresencePollSeconds;
        _settings.PhysicalCooldownMinutes = settings.PhysicalCooldownMinutes;
        _settings.PhysicalDailyLimit = settings.PhysicalDailyLimit;
        _client.UpdateSettings(_settings);
    }

    public void Dispose()
    {
        if (_disposed)
        {
            return;
        }
        _disposed = true;
        Stop();
        _shutdown.Cancel();
        _shutdown.Dispose();
        _client.Dispose();
    }

    private void OnTouchHead(object? sender, TouchEventArgs e)
    {
        _events.RecordTouchAsync("touch_head", e, _shutdown.Token).ForgetWithStatus(_host);
    }

    private void OnTouchBody(object? sender, TouchEventArgs e)
    {
        _events.RecordTouchAsync("touch_body", e, _shutdown.Token).ForgetWithStatus(_host);
    }

    private void OnFeed(object? sender, FeedEventArgs e)
    {
        _events.RecordFeedAsync(e.ItemName, _shutdown.Token).ForgetWithStatus(_host);
    }

    private void OnChatSubmitted(object? sender, ChatSubmittedEventArgs e)
    {
        if (string.IsNullOrWhiteSpace(e.Message))
        {
            return;
        }

        // 行为约束:发送瞬间本地播 thinking 动画,网络慢时不冻结 UI。
        _host.PlayThinking();
        SendChatAsync(e.Message, _shutdown.Token).ForgetWithStatus(_host);
    }

    private void OnSettingsRequested(object? sender, EventArgs e)
    {
        var view = new SettingsView();
        view.LoadFromSettings(_settings);
        view.SaveRequested += (_, e) => UpdateSettings(e.Settings);
        view.ShowDialog();
    }

    private async Task SendChatAsync(string message, CancellationToken cancellationToken)
    {
        var response = await _client.SendChatAsync(
            message,
            "user_chat",
            _host.CaptureBodyState(),
            cancellationToken).ConfigureAwait(false);
        _drain.DispatchBridgeResponse(response);
    }

    private async Task<VPetEventResponse> PostEventAndMaybeShowAsync(
        VPetEventRequest request,
        CancellationToken cancellationToken)
    {
        if (!_settings.TouchEscalation && request.WantReply)
        {
            request.WantReply = false;
        }

        var response = await _client.SendEventAsync(request, cancellationToken).ConfigureAwait(false);
        if (response.Replied)
        {
            _drain.DispatchBridgeResponse(response);
        }
        return response;
    }

    private async Task PollPresenceAsync(CancellationToken cancellationToken)
    {
        var response = await _presence.PollAsync(cancellationToken).ConfigureAwait(false);
        await _drain.DispatchAsync(response, cancellationToken).ConfigureAwait(false);
    }

    private Task PollDrainAsync(CancellationToken cancellationToken)
    {
        return _drain.PollPendingAsync(_presence, cancellationToken);
    }

    private async Task FlushEventsAsync(CancellationToken cancellationToken)
    {
        var responses = await _events.FlushExpiredAsync(cancellationToken).ConfigureAwait(false);
        foreach (var response in responses)
        {
            if (response.Replied)
            {
                _drain.DispatchBridgeResponse(response);
            }
        }
    }

    private void RunFireAndForget(Func<CancellationToken, Task> action)
    {
        action(_shutdown.Token).ForgetWithStatus(_host);
    }
}

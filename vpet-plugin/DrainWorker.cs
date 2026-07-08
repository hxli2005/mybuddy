using System;
using System.Threading;
using System.Threading.Tasks;

namespace MyBuddy.VPetPlugin;

public sealed class DrainWorker
{
    private readonly BridgeSettings _settings;
    private readonly BridgeClient _client;
    private readonly IVPetHost _host;
    private readonly ActionMapper _mapper;
    private DateTimeOffset _physicalDay = DateTimeOffset.Now.Date;
    private DateTimeOffset _lastPhysicalAt = DateTimeOffset.MinValue;
    private int _physicalCountToday;

    public DrainWorker(
        BridgeSettings settings,
        BridgeClient client,
        IVPetHost host,
        ActionMapper mapper)
    {
        _settings = settings;
        _client = client;
        _host = host;
        _mapper = mapper;
    }

    public async Task PollPendingAsync(PresenceGate presence, CancellationToken cancellationToken)
    {
        if (presence.IsDrainPaused)
        {
            return;
        }

        var payload = await _client.DrainAsync(digest: false, cancellationToken).ConfigureAwait(false);
        await DispatchAsync(payload, cancellationToken).ConfigureAwait(false);
    }

    public async Task DrainDigestAsync(CancellationToken cancellationToken)
    {
        var payload = await _client.DrainDigestAsync(cancellationToken).ConfigureAwait(false);
        await DispatchAsync(payload, cancellationToken).ConfigureAwait(false);
    }

    public Task DispatchAsync(VPetPendingResponse? payload, CancellationToken cancellationToken = default)
    {
        if (payload == null)
        {
            return Task.CompletedTask;
        }

        if (!string.IsNullOrWhiteSpace(payload.Digest?.Text))
        {
            // 行为约束:digest 后端模板拼接,这里只展示一句摘要,不逐条轰炸。
            _host.ShowBubble(payload.Digest.Text!, interrupt: false);
        }

        foreach (var item in payload.Events)
        {
            DispatchEvent(item);
        }
        return Task.CompletedTask;
    }

    public void DispatchBridgeResponse(VPetBridgeResponse response)
    {
        var text = response.Speech?.Text ?? response.Text;
        if (!string.IsNullOrWhiteSpace(text))
        {
            _host.ShowBubble(text!, response.Speech?.Interrupt == true);
        }
        ApplyAction(response.Action, response.Expression);

        foreach (var pending in response.Pending)
        {
            DispatchEvent(pending);
        }
    }

    private void DispatchEvent(VPetPendingEvent item)
    {
        var text = item.Speech?.Text ?? item.Text ?? "";
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }

        if (item.Speech?.Persistent == true)
        {
            // 行为约束:overdue reminder 持久可见但不打断。
            _host.ShowPersistentCard("提醒", text);
            ApplyAction(item.Action, item.Expression);
            return;
        }

        var interrupt = item.Speech?.Interrupt == true;
        if (interrupt && CanPhysicalInterrupt())
        {
            // 行为约束:physical_proactive 开时,仅非 overdue 的 interrupt 消息走到屏幕前。
            _host.MoveToForeground();
            _host.ShowBubble(text, interrupt: true);
            _lastPhysicalAt = DateTimeOffset.Now;
            _physicalCountToday += 1;
        }
        else
        {
            // 行为约束:physical_proactive 关、今天安静、全屏/演示、冷却中时只弹气泡。
            _host.ShowBubble(text, interrupt: false);
        }

        ApplyAction(item.Action, item.Expression);
    }

    private bool CanPhysicalInterrupt()
    {
        ResetDailyIfNeeded();
        if (!_settings.PhysicalProactive || _settings.TodayQuiet)
        {
            return false;
        }
        if (_host.IsPresentationOrFullScreenActive())
        {
            return false;
        }
        if (_physicalCountToday >= _settings.PhysicalDailyLimit)
        {
            return false;
        }
        return DateTimeOffset.Now - _lastPhysicalAt >= TimeSpan.FromMinutes(_settings.PhysicalCooldownMinutes);
    }

    private void ApplyAction(VPetAction? action, VPetExpression? expression)
    {
        var mapped = _mapper.Map(action?.Name, expression?.Name);
        _host.ApplyAction(mapped.ActionName);
        _host.ApplyExpression(mapped.ExpressionName);
    }

    private void ResetDailyIfNeeded()
    {
        var today = DateTimeOffset.Now.Date;
        if (today == _physicalDay.Date)
        {
            return;
        }
        _physicalDay = today;
        _physicalCountToday = 0;
        _lastPhysicalAt = DateTimeOffset.MinValue;
    }
}

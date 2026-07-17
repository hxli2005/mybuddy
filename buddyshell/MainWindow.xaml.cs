using BuddyShell.Anim;
using BuddyShell.Bridge;
using System.Globalization;
using System.IO;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;

namespace BuddyShell;

public enum ConnectionState { Connected, Warning, Error }

public partial class MainWindow : Window
{
    private readonly ShellSettings _settings = SettingsStore.Load();
    private readonly Outbox _outbox = new();
    private IAnimationController? _animationController;
    private BridgeClient? _client;
    private StateLoop? _stateLoop;
    private TouchLayer? _touchLayer;
    private Presence? _presence;
    private Notices? _notices;
    private Tray? _tray;
    private SpikeEvidence? _spikeEvidence;
    private VPetStateResponse? _state;
    private string? _pendingChatEventId;
    private string? _pendingChatText;
    private bool _chatSending;
    private bool _feeding;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        Closing += (_, _) => SaveWindowPosition();
        Closed += (_, _) => DisposeServices();
        DragBar.MouseLeftButtonDown += (_, args) =>
        {
            if (!IsInsideButton(args.OriginalSource as DependencyObject)) DragMove();
        };
        Chat.SendRequested += async (_, args) => await SendBodyChatAsync(args.Text);
        Foods.FoodSelected += async (_, args) =>
        {
            CloseDrawers();
            await FeedAsync(args.ItemId);
        };
    }

    public void SetConnectionState(string text, ConnectionState state)
    {
        Dispatcher.Invoke(() =>
        {
            StatusText.Text = text;
            StatusDot.Fill = state switch
            {
                ConnectionState.Connected => Brushes.LightGreen,
                ConnectionState.Warning => Brushes.Gold,
                _ => Brushes.IndianRed,
            };
        });
    }

    private void ToggleChat_Click(object sender, RoutedEventArgs e) =>
        ToggleDrawer(ChatDrawer);

    private void ToggleFood_Click(object sender, RoutedEventArgs e) =>
        ToggleDrawer(FoodDrawer);

    private void ToggleDrawer(FrameworkElement drawer)
    {
        var show = drawer.Visibility != Visibility.Visible;
        CloseDrawers();
        if (show) drawer.Visibility = Visibility.Visible;
    }

    private void CloseDrawers()
    {
        ChatDrawer.Visibility = Visibility.Collapsed;
        FoodDrawer.Visibility = Visibility.Collapsed;
    }

    private static bool IsInsideButton(DependencyObject? element)
    {
        while (element is not null)
        {
            if (element is System.Windows.Controls.Button) return true;
            element = VisualTreeHelper.GetParent(element);
        }
        return false;
    }

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        try
        {
            RestoreWindowPosition();
            var root = !string.IsNullOrWhiteSpace(_settings.PetAssetRoot)
                ? _settings.PetAssetRoot
                : AssetLocator.FindPetRoot();
            if (string.IsNullOrWhiteSpace(root)) throw new DirectoryNotFoundException("未找到 VPet 素材目录。");
            var renderer = new FramePlayerHost(root);
            _animationController = new AnimationController(AnimationManifest.CreateDefault(root), renderer);
            _animationController.Faulted += (_, args) => SetConnectionState(
                args.Recovered ? "动画渲染已恢复" : $"动画渲染失败：{args.Exception?.Message}",
                args.Recovered ? ConnectionState.Connected : ConnectionState.Error);
            AnimationHostSlot.Content = _animationController.View;
            UpdateAnimationBaseline();
            _spikeEvidence = new SpikeEvidence(_animationController);

            _client = new BridgeClient(_settings);
            _stateLoop = new StateLoop(_client);
            _stateLoop.Updated += OnStateUpdated;
            _stateLoop.Failed += (_, exception) => Dispatcher.Invoke(() =>
            {
                _state = null;
                SetConnectionState(exception.Message, ConnectionState.Warning);
            });
            _touchLayer = new TouchLayer(_animationController, _client, _outbox, ServerDate);
            _touchLayer.ResponseReceived += (_, response) => ShowSpeechOnly(response);
            _presence = new Presence(
                _client,
                _outbox,
                _settings,
                () => _settings.ActiveWorkSessionId,
                () => _state?.Physio?.Sleeping == true,
                () => _state is not null);
            _presence.UserReturned += async (_, _) => await UserBackAsync();
            _notices = new Notices(
                _client,
                _outbox,
                SpeechBubble,
                _settings,
                () => _state,
                () => _presence.IsFullscreen,
                () =>
                {
                    Show();
                    Activate();
                });
            _tray = CreateTray();

            _presence.Start();
            _notices.Start();
            _stateLoop.Start();
            SetConnectionState("动画状态机已运行", ConnectionState.Connected);
        }
        catch (Exception exception)
        {
            App.LogException(exception);
            SetConnectionState(exception.Message, ConnectionState.Error);
        }
    }

    private Tray CreateTray()
    {
        var tray = new Tray();
        tray.SetWorking(!string.IsNullOrWhiteSpace(_settings.ActiveWorkSessionId));
        tray.WorkToggled += async (_, _) => await ToggleWorkAsync();
        tray.QuietToggled += (_, _) => ToggleQuiet();
        tray.SettingsRequested += (_, _) => ShowSettings();
        tray.ShowRequested += (_, _) => { Show(); Activate(); };
        tray.ExitRequested += (_, _) => Close();
        return tray;
    }

    private void OnStateUpdated(object? sender, VPetStateResponse state)
    {
        Dispatcher.Invoke(() =>
        {
            _state = state;
            if (!string.Equals(state.Bridge, "vpet-bridge/2", StringComparison.Ordinal))
            {
                SetConnectionState($"协议不兼容：{state.Bridge}", ConnectionState.Error);
                return;
            }
            var settingsChanged =
                _settings.PhysioInjection != state.ServerFlags.PhysioInjection ||
                _settings.TouchEscalation != state.ServerFlags.TouchEscalation ||
                _settings.PhysicalProactive != state.ServerFlags.PhysicalProactive;
            _settings.PhysioInjection = state.ServerFlags.PhysioInjection;
            _settings.TouchEscalation = state.ServerFlags.TouchEscalation;
            _settings.PhysicalProactive = state.ServerFlags.PhysicalProactive;
            var quietBefore = (_settings.TodayQuiet, _settings.TodayQuietDate);
            if (DateTimeOffset.TryParse(state.ServerTime, out var serverTime))
            {
                _settings.NormalizeTodayQuiet(serverTime.ToString("yyyy-MM-dd"));
            }
            settingsChanged |= quietBefore != (_settings.TodayQuiet, _settings.TodayQuietDate);
            UpdateAnimationBaseline();
            SetConnectionState($"已连接 · {FormatServerTime(state.ServerTime)}", ConnectionState.Connected);
            App.LogMessage($"event=state server_time={state.ServerTime} idle_hint={state.IdleHint}");
            if (settingsChanged) SettingsStore.Save(_settings);
        });
        _ = _outbox.FlushAsync(_client!);
    }

    private async Task SendBodyChatAsync(string text)
    {
        if (_client is null || _animationController is null || _chatSending) return;
        _chatSending = true;
        if (!string.Equals(_pendingChatText, text, StringComparison.Ordinal))
        {
            _pendingChatText = text;
            _pendingChatEventId = $"chat-{Guid.NewGuid():N}";
        }
        var bodyEvent = new BodyEvent
        {
            EventId = _pendingChatEventId!,
            Content = text,
        };
        var correlationId = $"chat:{Guid.NewGuid():N}";
        _animationController.Submit(new AnimationRequest(
            AnimationIntent.Think,
            AnimationSource.Chat,
            correlationId,
            AnimationPriority.Think));
        try
        {
            var response = await _client.StepBodyAsync(new BodyStepRequest
            {
                ShownId = _settings.LastShownId,
                Event = bodyEvent,
            });
            if (response.EventStatus == "waiting_for_shown" && response.Expression is not null)
            {
                ShowBodyExpression(response.Expression);
                response = await _client.StepBodyAsync(new BodyStepRequest
                {
                    ShownId = _settings.LastShownId,
                    Event = bodyEvent,
                });
            }
            if (response.EventStatus is not ("processed" or "duplicate"))
            {
                throw new BridgeRequestException($"身体桥未接收聊天事件：{response.EventStatus}");
            }
            Chat.AcceptSent(text);
            _pendingChatText = null;
            _pendingChatEventId = null;
            var shownConfirmed = true;
            if (response.Expression is not null)
            {
                ShowBodyExpression(response.Expression);
                shownConfirmed = await ConfirmShownAsync();
            }
            App.LogMessage($"event=body_chat event_id={bodyEvent.EventId} status={response.EventStatus}");
            _animationController.Complete(
                correlationId,
                new AnimationOutcome(response.Expression is null ? null : AnimationIntent.Happy));
            if (shownConfirmed) SetConnectionState("已连接", ConnectionState.Connected);
        }
        catch (BridgeRequestException exception)
        {
            SetConnectionState(exception.StatusCode == 401
                    ? "令牌无效，请检查设置"
                    : "未连接，输入已保留",
                exception.StatusCode == 401 ? ConnectionState.Error : ConnectionState.Warning);
            if (exception.StatusCode == 401) ShowSettings();
            _animationController.Complete(
                correlationId,
                new AnimationOutcome(IsError: true, Reason: exception.Message));
        }
        catch (Exception exception)
        {
            App.LogException(exception);
            SetConnectionState("发送失败，输入已保留", ConnectionState.Error);
            _animationController.Complete(
                correlationId,
                new AnimationOutcome(IsError: true, Reason: exception.GetType().Name));
        }
        finally
        {
            _chatSending = false;
        }
    }

    private void ShowBodyExpression(PendingBodyExpression expression)
    {
        if (string.IsNullOrWhiteSpace(expression.Id) || string.IsNullOrWhiteSpace(expression.Text)) return;
        SpeechBubble.ShowSpeech(expression.Text, interrupt: true);
        Chat.AddAssistant(expression.Text);
        _settings.LastShownId = expression.Id;
        SettingsStore.Save(_settings);
        App.LogMessage($"event=bubble_shown expression_id={expression.Id} text={expression.Text}");
    }

    private async Task<bool> ConfirmShownAsync()
    {
        try
        {
            var confirmation = await _client!.StepBodyAsync(new BodyStepRequest
            {
                ShownId = _settings.LastShownId,
            });
            App.LogMessage(
                $"event=shown expression_id={_settings.LastShownId} " +
                $"confirmed={confirmation.ShownConfirmed}");
            return confirmation.ShownConfirmed;
        }
        catch (BridgeRequestException exception)
        {
            SetConnectionState("回复已显示，shown确认待重报", ConnectionState.Warning);
            App.LogMessage(
                $"event=shown_pending expression_id={_settings.LastShownId} reason={exception.Message}");
            return false;
        }
    }

    private async Task FeedAsync(string itemId)
    {
        if (_client is null || _animationController is null || _feeding) return;
        _feeding = true;
        // Default VPet item trajectories are 2675ms (eat) and 2750ms (drink).
        var animationFloor = Task.Delay(TimeSpan.FromMilliseconds(2800));
        var request = new VPetEventRequest
        {
            Event = "feed",
            Context = new() { ["item"] = itemId },
        };
        _animationController.Submit(new AnimationRequest(
            AnimationIntent.Eat,
            AnimationSource.Feed,
            request.ClientEventId,
            AnimationPriority.Feed,
            new Dictionary<string, string> { ["item"] = itemId }));
        try
        {
            var response = await _client.SendEventAsync(request);
            if (response.Speech is { Text.Length: > 0 } speech)
            {
                SpeechBubble.ShowSpeech(speech.Text, speech.Interrupt);
            }
            App.LogMessage(
                $"event=feed server_time={_state?.ServerTime} client_event_id={request.ClientEventId} " +
                $"item={itemId}");
            if (_stateLoop is not null) await _stateLoop.PollAsync();
        }
        catch (BridgeRequestException)
        {
            await _outbox.EnqueueAsync(request);
            SetConnectionState("投喂已暂存，恢复连接后补报", ConnectionState.Warning);
        }
        finally
        {
            await animationFloor;
            _feeding = false;
        }
    }

    private async Task UserBackAsync()
    {
        if (_client is null) return;
        var request = new VPetEventRequest
        {
            Event = "user_back",
            WantReply = _settings.PhysicalProactive && !_settings.TodayQuiet,
        };
        try
        {
            var response = await _client.SendEventAsync(request);
            App.LogMessage(
                $"event=user_back server_time={_state?.ServerTime} " +
                $"client_event_id={request.ClientEventId}");
            var displayedDigest = _notices is not null && await _notices.DrainAsync(digest: true);
            if (!displayedDigest && response.Speech is { Text.Length: > 0 })
            {
                ShowResponse(response);
                if (_notices is not null)
                {
                    await _notices.AcknowledgeExternalAsync("user_back");
                }
            }
        }
        catch (BridgeRequestException) { await _outbox.EnqueueAsync(request); }
    }

    private async Task ToggleWorkAsync()
    {
        if (_client is null) return;
        var stopping = !string.IsNullOrWhiteSpace(_settings.ActiveWorkSessionId);
        var sessionId = stopping ? _settings.ActiveWorkSessionId! : Guid.NewGuid().ToString("N");
        var request = new VPetEventRequest
        {
            Event = stopping ? "work_stop" : "work_start",
            Context = new() { ["session_id"] = sessionId },
        };
        try
        {
            var response = await _client.SendEventAsync(request);
            _settings.ActiveWorkSessionId = stopping ? null : sessionId;
            SettingsStore.Save(_settings);
            _tray?.SetWorking(!stopping);
            UpdateAnimationBaseline();
            if (response.Speech is { Text.Length: > 0 } speech)
            {
                SpeechBubble.ShowSpeech(speech.Text, speech.Interrupt);
            }
            App.LogMessage(
                $"event={request.Event} server_time={_state?.ServerTime} " +
                $"client_event_id={request.ClientEventId} session_id={sessionId}");
            if (_stateLoop is not null) await _stateLoop.PollAsync();
        }
        catch (BridgeRequestException exception)
        {
            await _outbox.EnqueueAsync(request);
            _settings.ActiveWorkSessionId = stopping ? null : sessionId;
            SettingsStore.Save(_settings);
            _tray?.SetWorking(!stopping);
            UpdateAnimationBaseline();
            SetConnectionState(exception.Message, ConnectionState.Warning);
        }
    }

    private void ShowResponse(VPetBridgeResponse response, bool submitAnimation = true)
    {
        Dispatcher.Invoke(() =>
        {
            if (response.Speech is { Text.Length: > 0 } speech)
            {
                SpeechBubble.ShowSpeech(speech.Text, speech.Interrupt);
            }
            var reaction = ActionMapper.TryFrom(response.Action?.Name, response.Expression?.Name);
            if (submitAnimation && reaction is { } intent && intent != AnimationIntent.Think)
            {
                _animationController?.Submit(new AnimationRequest(
                    intent,
                    AnimationSource.BridgeResponse,
                    $"response:{Guid.NewGuid():N}",
                    AnimationPriority.Response));
            }
        });
        if (response.Pending.Count > 0 && _notices is not null)
        {
            _ = _notices.DisplayPendingAsync(response.Pending);
        }
    }

    private void ShowSpeechOnly(VPetBridgeResponse response)
    {
        if (response.Speech is { Text.Length: > 0 } speech)
        {
            Dispatcher.Invoke(() => SpeechBubble.ShowSpeech(speech.Text, speech.Interrupt));
        }
    }

    private void ToggleQuiet()
    {
        var date = ServerDate();
        if (date is null)
        {
            SetConnectionState("尚未取得服务端日期，不能设置今天安静", ConnectionState.Warning);
            return;
        }
        _settings.TodayQuiet = !_settings.TodayQuiet;
        _settings.TodayQuietDate = _settings.TodayQuiet ? date : null;
        SettingsStore.Save(_settings);
        SetConnectionState(_settings.TodayQuiet ? "今天安静" : "主动气泡已恢复", ConnectionState.Connected);
    }

    private void ShowSettings()
    {
        var window = new SettingsWindow(_settings, _state) { Owner = this };
        if (window.ShowDialog() == true) _client?.UpdateSettings(_settings);
    }

    private void Settings_Click(object sender, RoutedEventArgs e) => ShowSettings();
    private void Exit_Click(object sender, RoutedEventArgs e) => Close();

    private void AnimationHost_Drop(object sender, DragEventArgs e)
    {
        if (e.Data.GetData(DataFormats.Text) is string item) _ = FeedAsync(item);
    }

    private string? ServerDate()
    {
        if (_state is null || !DateTimeOffset.TryParse(_state.ServerTime, CultureInfo.InvariantCulture,
                DateTimeStyles.RoundtripKind, out var time)) return null;
        return time.ToString("yyyy-MM-dd", CultureInfo.InvariantCulture);
    }

    private static string FormatServerTime(string value) =>
        DateTimeOffset.TryParse(value, out var time) ? time.ToString("MM-dd HH:mm") : value;

    private void UpdateAnimationBaseline()
    {
        var levels = _state?.Physio?.Levels ?? new PhysioLevelFlags();
        _animationController?.UpdateBaseline(new BaselineSnapshot(
            _state is not null,
            _state?.Physio?.Sleeping == true,
            !string.IsNullOrWhiteSpace(_settings.ActiveWorkSessionId),
            _state?.IdleHint ?? "idle",
            new PhysioLevels(levels.Hungry, levels.Tired, levels.Low, levels.Bright),
            _state?.Warmth ?? 0.5));
    }

    private void RestoreWindowPosition()
    {
        if (_settings.WindowLeft is not double left || _settings.WindowTop is not double top) return;
        // Window.Left/Top/Width/Height use WPF device-independent pixels. WinForms Screen
        // returns physical pixels in a per-monitor-DPI-aware process, so mixing both coordinate
        // systems can restore most of the window below the visible desktop.
        var area = SystemParameters.WorkArea;
        Left = Math.Clamp(left, area.Left, Math.Max(area.Left, area.Right - Width));
        Top = Math.Clamp(top, area.Top, Math.Max(area.Top, area.Bottom - Height));
    }

    private void SaveWindowPosition()
    {
        if (!double.IsFinite(Left) || !double.IsFinite(Top)) return;
        _settings.WindowLeft = Left;
        _settings.WindowTop = Top;
        SettingsStore.Save(_settings);
    }

    private void DisposeServices()
    {
        _notices?.Dispose();
        _spikeEvidence?.Dispose();
        _presence?.Dispose();
        _touchLayer?.Dispose();
        _stateLoop?.Dispose();
        _tray?.Dispose();
        _client?.Dispose();
        _animationController?.Dispose();
    }
}

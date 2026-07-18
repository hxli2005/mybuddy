using BuddyShell.Anim;
using BuddyShell.Bridge;
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
    private Presence? _presence;
    private Tray? _tray;
    private SpikeEvidence? _spikeEvidence;
    private VPetStateResponse? _state = null;
    private Dictionary<string, string>? _bodyBaseline;
    private string? _pendingChatEventId;
    private string? _pendingChatText;
    private bool _chatSending;
    private bool _touchReporting;
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
            _animationController.TouchDetected += OnTouchDetected;
            _animationController.Faulted += (_, args) => SetConnectionState(
                args.Recovered ? "动画渲染已恢复" : $"动画渲染失败：{args.Exception?.Message}",
                args.Recovered ? ConnectionState.Connected : ConnectionState.Error);
            AnimationHostSlot.Content = _animationController.View;
            UpdateAnimationBaseline();
            _spikeEvidence = new SpikeEvidence(_animationController);

            _client = new BridgeClient(_settings);
            _presence = new Presence(_settings);
            _stateLoop = new StateLoop(
                _client,
                () => _settings.LastShownId,
                () => _presence.Snapshot());
            _stateLoop.Updated += OnBodyUpdated;
            _stateLoop.Failed += (_, exception) => Dispatcher.Invoke(() =>
            {
                _bodyBaseline = null;
                UpdateAnimationBaseline();
                SetConnectionState(exception.Message, ConnectionState.Warning);
            });
            _tray = CreateTray();

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
        tray.SettingsRequested += (_, _) => ShowSettings();
        tray.ShowRequested += (_, _) => { Show(); Activate(); };
        tray.ExitRequested += (_, _) => Close();
        return tray;
    }

    private async void OnBodyUpdated(object? sender, BodyStepResponse response)
    {
        var displayed = false;
        Dispatcher.Invoke(() =>
        {
            UpdateBodyBaseline(response);
            if (response.Expression is not null &&
                !string.Equals(response.Expression.Id, _settings.LastShownId, StringComparison.Ordinal))
            {
                ShowBodyExpression(response.Expression);
                displayed = true;
            }
            SetConnectionState("已连接", ConnectionState.Connected);
            App.LogMessage(
                $"event=body_state baseline={response.Baseline.GetValueOrDefault("baseline", "idle")} " +
                $"activity={response.Baseline.GetValueOrDefault("current_activity", "")} " +
                $"time_status={response.TimeStatus}");
        });
        if (displayed) await ConfirmShownAsync();
    }

    private void UpdateBodyBaseline(BodyStepResponse response)
    {
        _bodyBaseline = response.Baseline;
        UpdateAnimationBaseline();
    }

    private async void OnTouchDetected(object? sender, TouchDetectedEventArgs args)
    {
        if (_client is null) return;
        if (_touchReporting)
        {
            App.LogMessage(
                $"event=body_touch_unreported event_id={args.CorrelationId} " +
                $"zone={args.Zone.ToString().ToLowerInvariant()} reason=step_busy");
            return;
        }
        _touchReporting = true;
        var bodyEvent = new BodyEvent
        {
            EventId = args.CorrelationId,
            Type = args.Zone == TouchZone.Head ? "touch_head" : "touch_body",
        };
        try
        {
            var response = await _client.StepBodyAsync(new BodyStepRequest
            {
                ShownId = _settings.LastShownId,
                Presence = _presence?.Snapshot(),
                Event = bodyEvent,
            });
            if (response.EventStatus == "waiting_for_shown" && response.Expression is not null)
            {
                if (!string.Equals(
                        response.Expression.Id,
                        _settings.LastShownId,
                        StringComparison.Ordinal))
                {
                    ShowBodyExpression(response.Expression);
                }
                response = await _client.StepBodyAsync(new BodyStepRequest
                {
                    ShownId = _settings.LastShownId,
                    Presence = _presence?.Snapshot(),
                    Event = bodyEvent,
                });
            }
            if (response.EventStatus is not ("processed" or "duplicate"))
            {
                App.LogMessage(
                    $"event=body_touch_dropped event_id={bodyEvent.EventId} " +
                    $"zone={args.Zone.ToString().ToLowerInvariant()} status={response.EventStatus}");
                return;
            }
            UpdateBodyBaseline(response);
            var shownConfirmed = true;
            if (response.Expression is not null)
            {
                ShowBodyExpression(response.Expression);
                shownConfirmed = await ConfirmShownAsync();
            }
            if (shownConfirmed) SetConnectionState("已连接", ConnectionState.Connected);
            App.LogMessage(
                $"event=body_touch event_id={bodyEvent.EventId} " +
                $"zone={args.Zone.ToString().ToLowerInvariant()} status={response.EventStatus}");
        }
        catch (BridgeRequestException exception)
        {
            SetConnectionState("未连接；这次触碰只做了本地反射", ConnectionState.Warning);
            App.LogMessage(
                $"event=body_touch_unreported event_id={bodyEvent.EventId} " +
                $"zone={args.Zone.ToString().ToLowerInvariant()} reason={exception.Message}");
        }
        catch (Exception exception)
        {
            App.LogException(exception);
            SetConnectionState("触碰没有报给心智；本地反射不受影响", ConnectionState.Warning);
        }
        finally
        {
            _touchReporting = false;
        }
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
                Presence = _presence?.Snapshot(),
                Event = bodyEvent,
            });
            if (response.EventStatus == "waiting_for_shown" && response.Expression is not null)
            {
                ShowBodyExpression(response.Expression);
                response = await _client.StepBodyAsync(new BodyStepRequest
                {
                    ShownId = _settings.LastShownId,
                    Presence = _presence?.Snapshot(),
                    Event = bodyEvent,
                });
            }
            if (response.EventStatus is not ("processed" or "duplicate"))
            {
                throw new BridgeRequestException($"身体桥未接收聊天事件：{response.EventStatus}");
            }
            UpdateBodyBaseline(response);
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
        SpeechBubble.ShowSpeech(
            expression.Text,
            interrupt: !string.Equals(expression.Kind, "ambient", StringComparison.Ordinal));
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
                Presence = _presence?.Snapshot(),
            });
            UpdateBodyBaseline(confirmation);
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

    private void UpdateAnimationBaseline()
    {
        var baseline = _bodyBaseline?.GetValueOrDefault("baseline", "idle") ?? "idle";
        _animationController?.UpdateBaseline(new BaselineSnapshot(
            _bodyBaseline is not null,
            string.Equals(baseline, "sleep", StringComparison.Ordinal),
            false,
            baseline,
            new PhysioLevels(false, false, false, false),
            0.5));
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
        _spikeEvidence?.Dispose();
        if (_animationController is not null) _animationController.TouchDetected -= OnTouchDetected;
        _stateLoop?.Dispose();
        _tray?.Dispose();
        _client?.Dispose();
        _animationController?.Dispose();
    }
}

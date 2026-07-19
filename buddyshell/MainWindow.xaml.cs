using BuddyShell.Anim;
using BuddyShell.Bridge;
using System.IO;
using System.Diagnostics;
using System.Windows;
using System.Windows.Input;
using System.Windows.Interop;
using System.Windows.Media;
using System.Windows.Threading;

namespace BuddyShell;

public enum ConnectionState { Connected, Warning, Error }

public partial class MainWindow : Window
{
    private readonly ShellSettings _settings = SettingsStore.Load();
    private readonly DispatcherTimer _walkTimer = new(DispatcherPriority.Render);
    private readonly Stopwatch _walkClock = new();
    private WalkAttempt? _walkAttempt;
    private EngineHost? _engine;
    private IAnimationController? _animationController;
    private BridgeClient? _client;
    private StateLoop? _stateLoop;
    private Presence? _presence;
    private Tray? _tray;
    private BodyActivityReceipt? _activityReceipt;
    private string? _activeActivityId;
    private string? _pendingChatEventId;
    private string? _pendingChatText;
    private bool _chatSending;
    private bool _touchReporting;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        _walkTimer.Interval = TimeSpan.FromMilliseconds(16);
        _walkTimer.Tick += OnWalkTick;
        Closing += (_, _) => SaveWindowPosition();
        Closed += (_, _) => DisposeServices();
        DragBar.MouseLeftButtonDown += (_, args) =>
        {
            if (!IsInsideButton(args.OriginalSource as DependencyObject)) DragMove();
        };
        Chat.SendRequested += async (_, args) => await SendBodyChatAsync(args.Text);
    }

    public void SetConnectionState(string text, ConnectionState state) => Dispatcher.Invoke(() =>
    {
        StatusText.Text = text;
        StatusDot.Fill = state switch
        {
            ConnectionState.Connected => Brushes.LightGreen,
            ConnectionState.Warning => Brushes.Gold,
            _ => Brushes.IndianRed,
        };
    });

    private void ToggleChat_Click(object sender, RoutedEventArgs e) =>
        ChatDrawer.Visibility = ChatDrawer.Visibility == Visibility.Visible
            ? Visibility.Collapsed
            : Visibility.Visible;

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
            if (string.IsNullOrWhiteSpace(root))
                throw new DirectoryNotFoundException("未找到 VPet 素材目录。");
            _animationController = new AnimationController(
                AnimationManifest.CreateDefault(root),
                new FramePlayerHost(root));
            _animationController.TouchDetected += OnTouchDetected;
            _animationController.ActivityFinished += OnActivityFinished;
            _animationController.Faulted += (_, args) => SetConnectionState(
                args.Recovered ? "动画渲染已恢复" : $"动画渲染失败：{args.Exception?.Message}",
                args.Recovered ? ConnectionState.Connected : ConnectionState.Error);
            AnimationHostSlot.Content = _animationController.View;

            _engine = EngineHost.Start(_settings);
            _client = new BridgeClient(_settings);
            _presence = new Presence(_settings);
            _stateLoop = new StateLoop(
                _client,
                () => _settings.LastShownId,
                () => _activityReceipt,
                () => _presence.Snapshot());
            _stateLoop.Updated += OnBodyUpdated;
            _stateLoop.Failed += (_, exception) => Dispatcher.Invoke(() =>
            {
                SetConnectionState(exception.Message, ConnectionState.Warning);
            });
            _tray = new Tray();
            _tray.SettingsRequested += (_, _) => ShowSettings();
            _tray.ShowRequested += (_, _) => { Show(); Activate(); };
            _tray.ExitRequested += (_, _) => Close();
            _stateLoop.Start();
        }
        catch (Exception exception)
        {
            App.LogException(exception);
            SetConnectionState(exception.Message, ConnectionState.Error);
        }
    }

    private async void OnBodyUpdated(object? sender, BodyStepResponse response)
    {
        var displayed = false;
        Dispatcher.Invoke(() =>
        {
            ApplyActivity(response);
            if (response.Expression is not null &&
                !string.Equals(response.Expression.Id, _settings.LastShownId, StringComparison.Ordinal))
            {
                ShowBodyExpression(response.Expression);
                displayed = true;
            }
            SetMindConnectionState(response);
        });
        if (displayed) await ConfirmShownAsync();
    }

    private async void OnTouchDetected(object? sender, TouchDetectedEventArgs args)
    {
        if (_client is null || _touchReporting) return;
        _touchReporting = true;
        var bodyEvent = new BodyEvent
        {
            EventId = args.CorrelationId,
            Type = args.Zone == TouchZone.Head ? "touch_head" : "touch_body",
        };
        try
        {
            var response = await StepAsync(bodyEvent);
            if (response.EventStatus is not ("processed" or "duplicate")) return;
            ApplyActivity(response);
            if (response.Expression is not null)
            {
                ShowBodyExpression(response.Expression);
                await ConfirmShownAsync();
            }
            SetMindConnectionState(response);
        }
        catch (BridgeRequestException exception)
        {
            SetConnectionState("未连接；这次触碰只做了本地反射", ConnectionState.Warning);
            App.LogMessage($"event=body_touch_unreported event_id={bodyEvent.EventId} reason={exception.Message}");
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
            Type = "chat",
            Content = text,
        };
        var animationId = $"chat:{Guid.NewGuid():N}";
        _animationController.Submit(new AnimationRequest(
            AnimationIntent.Think,
            AnimationSource.Chat,
            animationId,
            AnimationPriority.Think));
        try
        {
            var response = await StepAsync(bodyEvent);
            if (response.EventStatus is not ("processed" or "duplicate"))
                throw new BridgeRequestException($"身体桥未接收聊天事件：{response.EventStatus}");
            ApplyActivity(response);
            Chat.AcceptSent(text);
            _pendingChatText = null;
            _pendingChatEventId = null;
            if (response.Expression is not null)
            {
                ShowBodyExpression(response.Expression);
                await ConfirmShownAsync();
            }
            _animationController.Complete(animationId, new AnimationOutcome(AnimationIntent.Happy));
            SetMindConnectionState(response);
        }
        catch (BridgeRequestException exception)
        {
            SetConnectionState("未连接，输入已保留", ConnectionState.Warning);
            _animationController.Complete(animationId, new AnimationOutcome(IsError: true, Reason: exception.Message));
        }
        finally
        {
            _chatSending = false;
        }
    }

    private async Task<BodyStepResponse> StepAsync(BodyEvent bodyEvent)
    {
        var response = await _client!.StepBodyAsync(new BodyStepRequest
        {
            ShownId = _settings.LastShownId,
            ActivityReceipt = _activityReceipt,
            Presence = _presence?.Snapshot(),
            Event = bodyEvent,
        });
        if (response.EventStatus == "waiting_for_shown" && response.Expression is not null)
        {
            ShowBodyExpression(response.Expression);
            response = await _client.StepBodyAsync(new BodyStepRequest
            {
                ShownId = _settings.LastShownId,
                ActivityReceipt = _activityReceipt,
                Presence = _presence?.Snapshot(),
                Event = bodyEvent,
            });
        }
        return response;
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
            var response = await _client!.StepBodyAsync(new BodyStepRequest
            {
                ShownId = _settings.LastShownId,
                ActivityReceipt = _activityReceipt,
                Presence = _presence?.Snapshot(),
            });
            ApplyActivity(response);
            return response.ShownConfirmed;
        }
        catch (BridgeRequestException exception)
        {
            SetConnectionState("回复已显示，shown 确认待重报", ConnectionState.Warning);
            App.LogMessage($"event=shown_pending expression_id={_settings.LastShownId} reason={exception.Message}");
            return false;
        }
    }

    private void ApplyActivity(BodyStepResponse response)
    {
        if (response.ActivityConfirmed && _activityReceipt is not null)
        {
            _activityReceipt = null;
            _activeActivityId = null;
        }
        if (response.Activity is not { } activity ||
            string.Equals(activity.Id, _activeActivityId, StringComparison.Ordinal) ||
            string.Equals(activity.Id, _activityReceipt?.ActivityId, StringComparison.Ordinal))
            return;
        _activeActivityId = activity.Id;
        if (string.Equals(activity.Type, "read", StringComparison.Ordinal))
        {
            _animationController?.Submit(new AnimationRequest(
                AnimationIntent.Read,
                AnimationSource.State,
                activity.Id,
                AnimationPriority.Activity));
            return;
        }
        if (string.Equals(activity.Type, "walk", StringComparison.Ordinal))
        {
            StartWalk(activity.Id);
            return;
        }
        _activeActivityId = null;
    }

    private void StartWalk(string activityId)
    {
        try
        {
            _walkAttempt = new WalkAttempt(
                activityId,
                Left,
                Top,
                ActualWidth,
                ActualHeight,
                CurrentWorkArea());
            Left = _walkAttempt.Left;
            Top = _walkAttempt.Top;
            _walkClock.Restart();
            _walkTimer.Start();
            _animationController?.Submit(new AnimationRequest(
                _walkAttempt.Intent,
                AnimationSource.State,
                activityId,
                AnimationPriority.Activity));
        }
        catch (Exception exception)
        {
            FailWalk(activityId, exception);
        }
    }

    private void OnWalkTick(object? sender, EventArgs args)
    {
        if (_walkAttempt is null) return;
        try
        {
            _walkAttempt.Advance(_walkClock.ElapsedMilliseconds);
            Left = _walkAttempt.Left;
            Top = _walkAttempt.Top;
        }
        catch (Exception exception)
        {
            FailWalk(_walkAttempt.ActivityId, exception);
        }
    }

    private async void FailWalk(string activityId, Exception exception)
    {
        _walkTimer.Stop();
        _walkClock.Stop();
        _walkAttempt = null;
        _activeActivityId = null;
        _activityReceipt = new BodyActivityReceipt
        {
            ActivityId = activityId,
            Status = "failed",
            Reason = "window_fault",
        };
        SetConnectionState($"窗口移动失败：{exception.Message}", ConnectionState.Warning);
        if (_stateLoop is not null) await _stateLoop.PollAsync();
    }

    private async void OnActivityFinished(object? sender, ActivityFinishedEventArgs args)
    {
        if (!string.Equals(args.ActivityId, _activeActivityId, StringComparison.Ordinal)) return;
        BodyWalkMotion? motion = null;
        if (_walkAttempt?.ActivityId == args.ActivityId)
        {
            _walkAttempt.Advance(_walkClock.ElapsedMilliseconds);
            Left = _walkAttempt.Left;
            Top = _walkAttempt.Top;
            _walkTimer.Stop();
            _walkClock.Stop();
            if (_walkAttempt.Contains(Left, Top)) motion = _walkAttempt.Capture(Left, Top);
            else
            {
                args = new ActivityFinishedEventArgs(args.ActivityId, "failed", "window_fault");
            }
            _walkAttempt = null;
            SaveWindowPosition();
        }
        _activityReceipt = new BodyActivityReceipt
        {
            ActivityId = args.ActivityId,
            Status = args.Status,
            Reason = args.Reason,
            Motion = motion,
        };
        if (_stateLoop is not null) await _stateLoop.PollAsync();
    }

    private Rect CurrentWorkArea()
    {
        var handle = new WindowInteropHelper(this).Handle;
        var pixels = System.Windows.Forms.Screen.FromHandle(handle).WorkingArea;
        var transform = PresentationSource.FromVisual(this)?.CompositionTarget?.TransformFromDevice
            ?? Matrix.Identity;
        var topLeft = transform.Transform(new Point(pixels.Left, pixels.Top));
        var bottomRight = transform.Transform(new Point(pixels.Right, pixels.Bottom));
        return new Rect(topLeft, bottomRight);
    }

    private void SetMindConnectionState(BodyStepResponse response)
    {
        switch (response.MindStatus)
        {
            case "accepted":
                SetConnectionState("已连接", ConnectionState.Connected);
                break;
            case "rejected":
                SetConnectionState("已接住，但这次候选未通过校验", ConnectionState.Warning);
                break;
            case "unavailable":
                SetConnectionState("模型暂时不可用，这句是诚实的安全接住", ConnectionState.Warning);
                break;
            default:
                SetConnectionState("心智桥在线", ConnectionState.Connected);
                break;
        }
    }

    private void ShowSettings()
    {
        var window = new SettingsWindow(_settings) { Owner = this };
        if (window.ShowDialog() == true) _client?.UpdateSettings(_settings);
    }

    private void Settings_Click(object sender, RoutedEventArgs e) => ShowSettings();
    private void Exit_Click(object sender, RoutedEventArgs e) => Close();

    private void RestoreWindowPosition()
    {
        if (_settings.WindowLeft is not double left || _settings.WindowTop is not double top) return;
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
        if (_animationController is not null)
        {
            _animationController.TouchDetected -= OnTouchDetected;
            _animationController.ActivityFinished -= OnActivityFinished;
        }
        _stateLoop?.Dispose();
        _walkTimer.Stop();
        _walkTimer.Tick -= OnWalkTick;
        _walkClock.Stop();
        _tray?.Dispose();
        _client?.Dispose();
        _animationController?.Dispose();
        _engine?.Dispose();
    }
}

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
    private readonly DispatcherTimer _edgeHoverTimer = new()
    {
        Interval = TimeSpan.FromMilliseconds(350),
    };
    private readonly DispatcherTimer _edgeVisibilityTimer = new()
    {
        Interval = TimeSpan.FromMilliseconds(500),
    };
    private readonly Stopwatch _walkClock = new();
    private WalkAttempt? _walkAttempt;
    private EngineHost? _engine;
    private IAnimationController? _animationController;
    private FramePlayerHost? _animationHost;
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
    private bool _raiseDragging;
    private bool _raiseReporting;
    private EdgeSide? _edgeSide;
    private bool _edgePeeked;
    private bool _edgeHiddenForFullscreen;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        _walkTimer.Interval = TimeSpan.FromMilliseconds(16);
        _walkTimer.Tick += OnWalkTick;
        _edgeHoverTimer.Tick += OnEdgeHoverTick;
        _edgeVisibilityTimer.Tick += (_, _) => UpdateEdgeVisibility();
        MouseEnter += (_, _) =>
        {
            if (_edgeSide is not null && !_edgePeeked) _edgeHoverTimer.Start();
        };
        MouseLeave += (_, _) =>
        {
            _edgeHoverTimer.Stop();
            if (_edgePeeked) ReturnToEdgeMain();
        };
        PreviewMouseLeftButtonDown += (_, args) =>
        {
            if (_edgeSide is null) return;
            args.Handled = true;
            ExitEdge();
        };
        Closing += (_, _) => SaveWindowPosition();
        Closed += (_, _) => DisposeServices();
        DragBar.MouseLeftButtonDown += (_, args) =>
        {
            if (IsInsideButton(args.OriginalSource as DependencyObject)) return;
            args.Handled = true;
            BeginRaisedDrag();
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
            _animationHost = new FramePlayerHost(root);
            _animationHost.DragStarted += OnPetDragStarted;
            _animationController = new AnimationController(
                AnimationManifest.CreateDefault(root),
                _animationHost);
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
                () => _presence.Snapshot(_edgeSide is not null));
            _stateLoop.Updated += OnBodyUpdated;
            _stateLoop.Failed += (_, exception) => Dispatcher.Invoke(() =>
            {
                SetConnectionState(exception.Message, ConnectionState.Warning);
            });
            _tray = new Tray();
            _tray.SettingsRequested += (_, _) =>
            {
                if (_edgeSide is not null) ExitEdge();
                ShowSettings();
            };
            _tray.ShowRequested += (_, _) =>
            {
                if (_edgeSide is not null) ExitEdge();
                else { Show(); Activate(); }
            };
            _tray.ExitRequested += (_, _) => Close();
            RestoreEdgeMode();
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
            if (_edgeSide is null && response.Expression is not null &&
                !string.Equals(response.Expression.Id, _settings.LastShownId, StringComparison.Ordinal))
            {
                ShowBodyExpression(response.Expression);
                displayed = true;
            }
            SetMindConnectionState(response);
        });
        if (displayed) await ConfirmShownAsync();
    }

    private void OnPetDragStarted(object? sender, EventArgs args) => BeginRaisedDrag();

    private async void BeginRaisedDrag()
    {
        if (_raiseDragging || _animationController is null) return;
        _raiseDragging = true;
        string? eventId = null;
        var released = false;
        EdgeSide? dockSide = null;
        try
        {
            var animation = BodyActionCatalog.Default.Get("raise")
                .Animation(BodyActionDirection.Still);
            eventId = $"raise-{Guid.NewGuid():N}";
            _animationController.BeginInteractive(new AnimationRequest(
                animation.Intent,
                AnimationSource.DirectManipulation, eventId));
            var start = new Point(Left, Top);
            DragMove();
            released = Math.Abs(Left - start.X) >= SystemParameters.MinimumHorizontalDragDistance ||
                Math.Abs(Top - start.Y) >= SystemParameters.MinimumVerticalDragDistance;
            if (released) dockSide = EdgeDock.Detect(CurrentWorkArea(), Left, ActualWidth);
        }
        catch (InvalidOperationException exception)
        {
            SetConnectionState($"窗口拖动失败：{exception.Message}", ConnectionState.Warning);
        }
        finally
        {
            if (eventId is not null && dockSide is null)
                _animationController.EndInteractive(eventId);
            SaveWindowPosition();
            _raiseDragging = false;
        }
        if (dockSide is { } side)
        {
            EnterEdge(side);
            return;
        }
        if (released && eventId is not null) await ReportRaiseAsync(eventId);
    }

    private async Task ReportRaiseAsync(string eventId)
    {
        if (_client is null || _raiseReporting)
        {
            App.LogMessage($"event=body_raise_unreported event_id={eventId} reason=not_connected_or_busy");
            return;
        }
        _raiseReporting = true;
        var bodyEvent = new BodyEvent { EventId = eventId, Type = "raise" };
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
            SetConnectionState("未连接；这次提起只做了本地反射", ConnectionState.Warning);
            App.LogMessage($"event=body_raise_unreported event_id={eventId} reason={exception.Message}");
        }
        finally
        {
            _raiseReporting = false;
        }
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
            AnimationSource.Chat, animationId));
        try
        {
            var response = await StepAsync(bodyEvent);
            if (response.EventStatus is not ("processed" or "duplicate"))
                throw new BridgeRequestException($"身体桥未接收聊天事件：{response.EventStatus}");
            Chat.AcceptSent(text);
            _pendingChatText = null;
            _pendingChatEventId = null;
            _animationController.Complete(animationId, new AnimationOutcome(AnimationIntent.Happy));
            ApplyActivity(response);
            if (response.Expression is not null)
            {
                ShowBodyExpression(response.Expression);
                await ConfirmShownAsync();
            }
            SetMindConnectionState(response);
        }
        catch (BridgeRequestException)
        {
            SetConnectionState("未连接，输入已保留", ConnectionState.Warning);
            _animationController.Complete(animationId, new AnimationOutcome());
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
            Presence = _presence?.Snapshot(_edgeSide is not null),
            Event = bodyEvent,
        });
        if (response.EventStatus == "waiting_for_shown" && response.Expression is not null)
        {
            ShowBodyExpression(response.Expression);
            response = await _client.StepBodyAsync(new BodyStepRequest
            {
                ShownId = _settings.LastShownId,
                ActivityReceipt = _activityReceipt,
                Presence = _presence?.Snapshot(_edgeSide is not null),
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
                Presence = _presence?.Snapshot(_edgeSide is not null),
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
        if (_edgeSide is not null) return;
        if (response.Activity is not { } activity ||
            string.Equals(activity.Id, _activeActivityId, StringComparison.Ordinal) ||
            string.Equals(activity.Id, _activityReceipt?.ActivityId, StringComparison.Ordinal))
            return;
        if (!BodyActionCatalog.Default.TryGet(activity.Type, out var action) || action is null) return;
        _activeActivityId = activity.Id;
        if (action.Shape == BodyActionShape.Stationary)
        {
            _animationController?.Submit(new AnimationRequest(
                action.Animation(BodyActionDirection.Still).Intent,
                AnimationSource.State, activity.Id, activity.DurationMs));
            return;
        }
        if (action.Shape == BodyActionShape.Horizontal)
        {
            StartWalk(activity.Id, action);
            return;
        }
        _activeActivityId = null;
    }

    private void StartWalk(string activityId, BodyActionDefinition action)
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
                action.Animation(_walkAttempt.Direction).Intent,
                AnimationSource.State, activityId));
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

    private void RestoreEdgeMode()
    {
        var side = _settings.EdgeSide?.ToLowerInvariant() switch
        {
            "left" => EdgeSide.Left,
            "right" => EdgeSide.Right,
            _ => (EdgeSide?)null,
        };
        if (side is not null) EnterEdge(side.Value, restoring: true);
    }

    private void EnterEdge(EdgeSide side, bool restoring = false)
    {
        var area = CurrentWorkArea();
        if (_edgeSide is null) SaveWindowPosition();
        _edgeSide = side;
        var topRatio = restoring
            ? Math.Clamp(_settings.EdgeTopRatio ?? 0.5, 0, 1)
            : EdgeDock.TopRatio(area, ActualHeight, Top);
        var top = EdgeDock.TopFromRatio(area, ActualHeight, topRatio);
        var position = EdgeDock.Place(side, area, ActualWidth, ActualHeight, top);
        Left = position.X;
        Top = position.Y;
        _settings.EdgeSide = side.ToString().ToLowerInvariant();
        _settings.EdgeTopRatio = topRatio;
        SettingsStore.Save(_settings);

        SpeechBubble.Visibility = Visibility.Collapsed;
        ChatDrawer.Visibility = Visibility.Collapsed;
        DragBar.Visibility = Visibility.Collapsed;
        PetShadow.Visibility = Visibility.Collapsed;
        _animationController?.BeginInteractive(EdgeAnimationRequest(side, rise: false));
        _edgePeeked = false;
        _edgeVisibilityTimer.Start();
        UpdateEdgeVisibility();
        _ = _stateLoop?.PollAsync();
        App.LogMessage($"event=edge_enter side={_settings.EdgeSide} top_ratio={topRatio:F3}");
    }

    private void ExitEdge()
    {
        if (_edgeSide is not { } side) return;
        var area = CurrentWorkArea();
        var top = EdgeDock.TopFromRatio(area, ActualHeight, _settings.EdgeTopRatio ?? 0.5);
        _edgeSide = null;
        _edgeHoverTimer.Stop();
        _edgeVisibilityTimer.Stop();
        _edgePeeked = false;
        _edgeHiddenForFullscreen = false;
        Opacity = 1;
        IsHitTestVisible = true;
        SpeechBubble.Visibility = Visibility.Visible;
        DragBar.Visibility = Visibility.Visible;
        PetShadow.Visibility = Visibility.Visible;
        Left = side == EdgeSide.Left
            ? area.Left + 12
            : Math.Max(area.Left, area.Right - ActualWidth - 12);
        Top = Math.Clamp(top, area.Top, Math.Max(area.Top, area.Bottom - ActualHeight));
        if (_animationController is not null)
        {
            var reveal = EdgeAnimationRequest(side, rise: false);
            _animationController.BeginInteractive(reveal, resumeBody: true);
            _animationController.EndInteractive(reveal.CorrelationId);
        }
        _settings.EdgeSide = null;
        _settings.EdgeTopRatio = null;
        SaveWindowPosition();
        Show();
        Activate();
        _ = _stateLoop?.PollAsync();
        App.LogMessage($"event=edge_exit side={side.ToString().ToLowerInvariant()}");
    }

    private void OnEdgeHoverTick(object? sender, EventArgs args)
    {
        _edgeHoverTimer.Stop();
        if (_edgeSide is not { } side || !IsMouseOver || _edgeHiddenForFullscreen) return;
        _edgePeeked = true;
        _animationController?.BeginInteractive(EdgeAnimationRequest(side, rise: true));
        App.LogMessage($"event=edge_peek side={side.ToString().ToLowerInvariant()}");
    }

    private void ReturnToEdgeMain()
    {
        _edgePeeked = false;
        if (_edgeSide is not { } side || _animationController is not { } controller ||
            controller.Snapshot.CorrelationId is not { } current) return;
        controller.EndInteractive(current, EdgeAnimationRequest(side, rise: false), followUpResumeBody: true);
    }

    private static AnimationRequest EdgeAnimationRequest(EdgeSide side, bool rise) => new(
        rise
            ? side == EdgeSide.Left ? AnimationIntent.EdgeLeftRise : AnimationIntent.EdgeRightRise
            : side == EdgeSide.Left ? AnimationIntent.EdgeLeft : AnimationIntent.EdgeRight,
        AnimationSource.DirectManipulation, $"edge:{side.ToString().ToLowerInvariant()}:{(rise ? "rise" : "main")}:{Guid.NewGuid():N}");

    private void UpdateEdgeVisibility()
    {
        if (_edgeSide is null || _presence is null) return;
        var hidden = _presence.Snapshot(edgeDocked: true).Fullscreen;
        if (hidden == _edgeHiddenForFullscreen) return;
        _edgeHiddenForFullscreen = hidden;
        Opacity = hidden ? 0 : 1;
        IsHitTestVisible = !hidden;
        if (hidden && _edgePeeked) ReturnToEdgeMain();
        App.LogMessage($"event=edge_fullscreen hidden={hidden.ToString().ToLowerInvariant()}");
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
        if (_edgeSide is { })
        {
            _settings.EdgeTopRatio = EdgeDock.TopRatio(CurrentWorkArea(), ActualHeight, Top);
            SettingsStore.Save(_settings);
            return;
        }
        _settings.WindowLeft = Left;
        _settings.WindowTop = Top;
        SettingsStore.Save(_settings);
    }

    private void DisposeServices()
    {
        if (_animationHost is not null) _animationHost.DragStarted -= OnPetDragStarted;
        if (_animationController is not null)
        {
            _animationController.TouchDetected -= OnTouchDetected;
            _animationController.ActivityFinished -= OnActivityFinished;
        }
        _stateLoop?.Dispose();
        _walkTimer.Stop();
        _walkTimer.Tick -= OnWalkTick;
        _edgeHoverTimer.Stop();
        _edgeVisibilityTimer.Stop();
        _walkClock.Stop();
        _tray?.Dispose();
        _client?.Dispose();
        _animationController?.Dispose();
        _engine?.Dispose();
    }
}

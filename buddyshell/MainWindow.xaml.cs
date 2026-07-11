using BuddyShell.Anim;
using BuddyShell.Bridge;
using System.Globalization;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;

namespace BuddyShell;

public enum ConnectionState { Connected, Warning, Error }

public partial class MainWindow : Window
{
    private readonly ShellSettings _settings = SettingsStore.Load();
    private readonly Outbox _outbox = new();
    private IAnimationHost? _animationHost;
    private BridgeClient? _client;
    private StateLoop? _stateLoop;
    private TouchLayer? _touchLayer;
    private Presence? _presence;
    private Notices? _notices;
    private Tray? _tray;
    private SpikeEvidence? _spikeEvidence;
    private VPetStateResponse? _state;
    private string? _lastTurnId;
    private AnimationIntent? _lastBaseline;

    public MainWindow()
    {
        InitializeComponent();
        Loaded += OnLoaded;
        Closing += (_, _) => SaveWindowPosition();
        Closed += (_, _) => DisposeServices();
        DragBar.MouseLeftButtonDown += (_, args) =>
        {
            if (args.OriginalSource is not System.Windows.Controls.Button) DragMove();
        };
        Chat.SendRequested += async (_, args) => await SendChatAsync(args.Text);
        Foods.FoodSelected += async (_, args) => await FeedAsync(args.ItemId);
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

    private void OnLoaded(object sender, RoutedEventArgs e)
    {
        try
        {
            RestoreWindowPosition();
            var root = !string.IsNullOrWhiteSpace(_settings.PetAssetRoot)
                ? _settings.PetAssetRoot
                : AssetLocator.FindPetRoot();
            var forceFrame = _settings.ForceFramePlayer || string.Equals(
                Environment.GetEnvironmentVariable("BUDDYSHELL_FORCE_FRAME"), "1", StringComparison.Ordinal);
            _animationHost = forceFrame ? new FramePlayerHost(root!) : new VPetCoreHost(root!);
            AnimationHostSlot.Content = _animationHost.View;
            _animationHost.SetBaseline(new PhysioLevels(false, false, false, false), 0.5);
            _animationHost.Play(AnimationIntent.Idle, loop: true);
            _spikeEvidence = new SpikeEvidence(_animationHost);

            _client = new BridgeClient(_settings);
            _stateLoop = new StateLoop(_client);
            _stateLoop.Updated += OnStateUpdated;
            _stateLoop.Failed += (_, exception) => Dispatcher.Invoke(() =>
            {
                _state = null;
                SetConnectionState(exception.Message, ConnectionState.Warning);
            });
            _touchLayer = new TouchLayer(_animationHost, _client, _outbox, ServerDate);
            _touchLayer.ResponseReceived += (_, response) => ShowResponse(response);
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
            SetConnectionState(forceFrame ? "兼容帧播放器已运行" : "VPet.Core 已运行", ConnectionState.Connected);
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
        tray.FeedbackRequested += async (_, args) => await SendFeedbackAsync(args.Label);
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
            var levels = state.Physio?.Levels ?? new PhysioLevelFlags();
            _animationHost?.SetBaseline(
                new PhysioLevels(levels.Hungry, levels.Tired, levels.Low, levels.Bright), state.Warmth);
            var intent = state.Physio?.Sleeping == true
                ? AnimationIntent.Sleep
                : ActionMapper.From(null, null, state.IdleHint);
            if (_lastBaseline != intent)
            {
                _animationHost?.Play(intent, loop: true);
                _lastBaseline = intent;
            }
            SetConnectionState($"已连接 · {FormatServerTime(state.ServerTime)}", ConnectionState.Connected);
            App.LogMessage($"event=state server_time={state.ServerTime} idle_hint={state.IdleHint}");
            if (settingsChanged) SettingsStore.Save(_settings);
        });
        _ = _outbox.FlushAsync(_client!);
    }

    private async Task SendChatAsync(string text)
    {
        if (_client is null || _animationHost is null) return;
        Chat.AddUser(text);
        _animationHost.Play(AnimationIntent.Think, loop: true);
        try
        {
            var response = await _client.SendChatAsync(text);
            _lastTurnId = response.TurnId;
            App.LogMessage($"event=chat server_time={_state?.ServerTime} turn_id={response.TurnId}");
            var full = response.Text ?? response.Speech?.Text ?? "";
            if (!string.IsNullOrWhiteSpace(full)) Chat.AddAssistant(full);
            ShowResponse(response);
            if (response.Speech?.Truncated == true)
            {
                SetConnectionState("气泡已缩短，全文在聊天窗", ConnectionState.Connected);
            }
            if (_stateLoop is not null) await _stateLoop.PollAsync();
        }
        catch (BridgeRequestException exception)
        {
            SpeechBubble.ShowSpeech("这会儿没连上，我先在这儿陪你。", interrupt: true);
            SetConnectionState(exception.StatusCode == 401 ? "令牌无效，请检查设置" : exception.Message,
                exception.StatusCode == 401 ? ConnectionState.Error : ConnectionState.Warning);
            if (exception.StatusCode == 401) ShowSettings();
            _animationHost.Play(_lastBaseline ?? AnimationIntent.Idle, loop: true);
        }
    }

    private async Task FeedAsync(string itemId)
    {
        if (_client is null || _animationHost is null) return;
        _animationHost.Play(AnimationIntent.Eat);
        var request = new VPetEventRequest
        {
            Event = "feed",
            Context = new() { ["item"] = itemId },
        };
        try
        {
            ShowResponse(await _client.SendEventAsync(request));
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
            ShowResponse(response);
            App.LogMessage(
                $"event={request.Event} server_time={_state?.ServerTime} " +
                $"client_event_id={request.ClientEventId} session_id={sessionId}");
            if (!stopping) _animationHost?.Play(AnimationIntent.Work, loop: true);
            if (_stateLoop is not null) await _stateLoop.PollAsync();
        }
        catch (BridgeRequestException exception)
        {
            await _outbox.EnqueueAsync(request);
            _settings.ActiveWorkSessionId = stopping ? null : sessionId;
            SettingsStore.Save(_settings);
            _tray?.SetWorking(!stopping);
            if (!stopping) _animationHost?.Play(AnimationIntent.Work, loop: true);
            SetConnectionState(exception.Message, ConnectionState.Warning);
        }
    }

    private async Task SendFeedbackAsync(string label)
    {
        if (_client is null) return;
        try
        {
            await _client.SendFeedbackAsync(label, _lastTurnId);
            SetConnectionState("这条反馈记下了", ConnectionState.Connected);
        }
        catch (Exception exception) { SetConnectionState(exception.Message, ConnectionState.Warning); }
    }

    private void ShowResponse(VPetBridgeResponse response)
    {
        Dispatcher.Invoke(() =>
        {
            if (response.Speech is { Text.Length: > 0 } speech)
            {
                SpeechBubble.ShowSpeech(speech.Text, speech.Interrupt);
            }
            _animationHost?.Play(
                ActionMapper.From(response.Action?.Name, response.Expression?.Name), loop: false);
        });
        if (response.Pending.Count > 0 && _notices is not null)
        {
            _ = _notices.DisplayPendingAsync(response.Pending);
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

    private void RestoreWindowPosition()
    {
        if (_settings.WindowLeft is not double left || _settings.WindowTop is not double top) return;
        var area = System.Windows.Forms.Screen.AllScreens
            .Select(screen => screen.WorkingArea)
            .FirstOrDefault(rect => rect.Contains((int)left, (int)top));
        if (area.Width == 0) area = System.Windows.Forms.Screen.PrimaryScreen?.WorkingArea ?? new(0, 0, 1920, 1080);
        Left = Math.Clamp(left, area.Left, Math.Max(area.Left, area.Right - Width));
        Top = Math.Clamp(top, area.Top, Math.Max(area.Top, area.Bottom - Height));
    }

    private void SaveWindowPosition()
    {
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
        _animationHost?.Dispose();
    }
}

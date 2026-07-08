using System;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Windows;
using System.Windows.Controls;
using VPet_Simulator.Core;
using VPet_Simulator.Windows.Interface;
using static VPet_Simulator.Core.GraphInfo;

namespace MyBuddy.VPetPlugin;

public sealed class VPetHostAdapter : IVPetHost
{
    private readonly IMainWindow _mw;
    private MenuItem? _settingsMenuItem;
    private Action? _touchHeadHandler;
    private Action? _touchBodyHandler;
    private Action<Food>? _takeItemHandler;

    public VPetHostAdapter(IMainWindow mainWindow)
    {
        _mw = mainWindow;
    }

    public event EventHandler<TouchEventArgs>? TouchHead;
    public event EventHandler<TouchEventArgs>? TouchBody;
    public event EventHandler<FeedEventArgs>? Feed;
    public event EventHandler<ChatSubmittedEventArgs>? ChatSubmitted;
    public event EventHandler? SettingsRequested;

    public bool IsWindowDragInProgress
    {
        get
        {
            var display = _mw.Main.DisplayType;
            return _mw.Main.isPress
                || display.Type is GraphType.Move or GraphType.Raised_Dynamic or GraphType.Raised_Static
                || (display.Name?.StartsWith("raise", StringComparison.OrdinalIgnoreCase) == true)
                || (display.Name?.StartsWith("raised", StringComparison.OrdinalIgnoreCase) == true);
        }
    }

    public void Attach()
    {
        if (_settingsMenuItem == null)
        {
            var modConfig = _mw.Main.ToolBar.MenuMODConfig;
            modConfig.Visibility = Visibility.Visible;
            _settingsMenuItem = new MenuItem
            {
                Header = "MyBuddy Bridge",
                HorizontalContentAlignment = HorizontalAlignment.Center,
            };
            _settingsMenuItem.Click += (_, _) => RaiseSettingsRequested();
            modConfig.Items.Add(_settingsMenuItem);
        }

        if (_touchHeadHandler == null)
        {
            _touchHeadHandler = () => RaiseTouchHead(new TouchEventArgs(
                DateTimeOffset.Now,
                IsWindowDragInProgress,
                TimeSpan.Zero));
            _mw.Main.Event_TouchHead += _touchHeadHandler;
        }
        if (_touchBodyHandler == null)
        {
            _touchBodyHandler = () => RaiseTouchBody(new TouchEventArgs(
                DateTimeOffset.Now,
                IsWindowDragInProgress,
                TimeSpan.Zero));
            _mw.Main.Event_TouchBody += _touchBodyHandler;
        }
        if (_takeItemHandler == null)
        {
            _takeItemHandler = OnTakeItem;
            _mw.Event_TakeItem += _takeItemHandler;
        }
    }

    public void Detach()
    {
        if (_touchHeadHandler != null)
        {
            _mw.Main.Event_TouchHead -= _touchHeadHandler;
            _touchHeadHandler = null;
        }
        if (_touchBodyHandler != null)
        {
            _mw.Main.Event_TouchBody -= _touchBodyHandler;
            _touchBodyHandler = null;
        }
        if (_takeItemHandler != null)
        {
            _mw.Event_TakeItem -= _takeItemHandler;
            _takeItemHandler = null;
        }
        if (_settingsMenuItem != null)
        {
            _mw.Main.ToolBar.MenuMODConfig.Items.Remove(_settingsMenuItem);
            _settingsMenuItem = null;
        }
    }

    public BodyState? CaptureBodyState()
    {
        var save = _mw.Core.Save;
        return new BodyState
        {
            Food = ReadNumber(save, "Food", "StrengthFood"),
            Drink = ReadNumber(save, "Drink", "StrengthDrink"),
            Feeling = ReadNumber(save, "Feeling"),
            Health = ReadNumber(save, "Health"),
            Strength = ReadNumber(save, "Strength"),
            Likability = ReadNumber(save, "Likability"),
            Money = ReadNumber(save, "Money"),
            Mode = ReadText(save, "Mode"),
        };
    }

    public bool IsPresentationOrFullScreenActive()
    {
        if (!RuntimeInformation.IsOSPlatform(OSPlatform.Windows))
        {
            return false;
        }

        var foreground = GetForegroundWindow();
        if (foreground == IntPtr.Zero || !GetWindowRect(foreground, out var windowRect))
        {
            return false;
        }
        if (windowRect.Width <= 0 || windowRect.Height <= 0)
        {
            return false;
        }

        var monitor = MonitorFromWindow(foreground, MonitorDefaultToNearest);
        if (monitor == IntPtr.Zero)
        {
            return false;
        }
        var monitorInfo = new MonitorInfo
        {
            CbSize = (uint)Marshal.SizeOf<MonitorInfo>(),
        };
        return GetMonitorInfo(monitor, ref monitorInfo)
            && CoversMonitor(windowRect, monitorInfo.RcMonitor);
    }

    public void PlayThinking()
    {
        OnMainThread(() =>
        {
            if (_mw.Main.IsIdel)
            {
                _mw.Main.DisplayIdel_StateONE?.Invoke();
            }
        });
    }

    public void ShowBubble(string text, bool interrupt = false)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        OnMainThread(() => _mw.Main.Say(text));
    }

    public void ShowPersistentCard(string title, string text)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        OnMainThread(() => _mw.Main.LabelDisplayShow($"{title}: {text}", 15000));
    }

    public void MoveToForeground()
    {
        OnMainThread(() =>
        {
            var owner = GetOwnerWindow();
            if (owner == null)
            {
                return;
            }
            if (owner.WindowState == WindowState.Minimized)
            {
                owner.WindowState = WindowState.Normal;
            }
            owner.Activate();
            owner.Topmost = true;
            owner.Topmost = false;
        });
    }

    public void ApplyAction(string actionName)
    {
        if (string.IsNullOrWhiteSpace(actionName))
        {
            return;
        }
        OnMainThread(() => ApplyActionCore(actionName.Trim()));
    }

    public void ApplyExpression(string expressionName)
    {
        if (string.IsNullOrWhiteSpace(expressionName))
        {
            return;
        }
        OnMainThread(() => ApplyExpressionCore(expressionName.Trim()));
    }

    public void ShowBridgeStatus(string text, BridgeStatusKind kind)
    {
        if (string.IsNullOrWhiteSpace(text))
        {
            return;
        }
        var prefix = kind == BridgeStatusKind.Error ? "MyBuddy error: " : "MyBuddy: ";
        OnMainThread(() => _mw.Main.LabelDisplayShow(prefix + text, 5000));
    }

    public void RaiseTouchHead(TouchEventArgs e) => TouchHead?.Invoke(this, e);
    public void RaiseTouchBody(TouchEventArgs e) => TouchBody?.Invoke(this, e);
    public void RaiseFeed(FeedEventArgs e) => Feed?.Invoke(this, e);
    public void RaiseChatSubmitted(ChatSubmittedEventArgs e) => ChatSubmitted?.Invoke(this, e);
    public void RaiseSettingsRequested() => SettingsRequested?.Invoke(this, EventArgs.Empty);

    private void OnTakeItem(Food item)
    {
        var name = string.IsNullOrWhiteSpace(item.TranslateName) ? item.Name : item.TranslateName;
        RaiseFeed(new FeedEventArgs(DateTimeOffset.Now, name));
    }

    private void OnMainThread(Action action)
    {
        if (_mw.Main.Dispatcher.CheckAccess())
        {
            action();
            return;
        }
        _mw.Main.Dispatcher.Invoke(action);
    }

    private Window? GetOwnerWindow()
    {
        return Window.GetWindow(_mw.MGHost)
            ?? Window.GetWindow(_mw.PetGrid)
            ?? Window.GetWindow(_mw.Main);
    }

    private void ApplyActionCore(string actionName)
    {
        var normalized = actionName.ToLowerInvariant();
        if (TryDisplayNamedAnimation(actionName))
        {
            return;
        }

        switch (normalized)
        {
            case "thinking":
            case "curious":
                _mw.Main.DisplayIdel_StateONE?.Invoke();
                break;
            case "happy":
            case "greet":
                if (_mw.Main.DisplayIdel?.Invoke() != true)
                {
                    _mw.Main.DisplayDefault();
                }
                break;
            case "idle":
            case "talk":
            case "comfort":
            case "concern":
            case "notify":
            case "alert":
            case "remind":
            case "react":
            case "safety":
            case "serious":
                _mw.Main.DisplayDefault();
                break;
        }
    }

    private void ApplyExpressionCore(string expressionName)
    {
        var normalized = expressionName.ToLowerInvariant();
        if (TryDisplayNamedAnimation(expressionName))
        {
            return;
        }

        switch (normalized)
        {
            case "thinking":
            case "curious":
            case "worried":
            case "alert":
            case "serious":
                _mw.Main.DisplayIdel_StateONE?.Invoke();
                break;
            case "happy":
            case "smile":
                if (_mw.Main.DisplayIdel?.Invoke() != true)
                {
                    _mw.Main.DisplayDefault();
                }
                break;
        }
    }

    private bool TryDisplayNamedAnimation(string name)
    {
        var graph = _mw.Core.Graph.FindGraph(name, AnimatType.Single, _mw.Core.Save.Mode)
            ?? _mw.Core.Graph.FindGraph(name, AnimatType.A_Start, _mw.Core.Save.Mode);
        if (graph == null)
        {
            return false;
        }
        _mw.Main.Display(graph, _mw.Main.DisplayToNomal);
        return true;
    }

    private static double? ReadNumber(object target, params string[] names)
    {
        foreach (var name in names)
        {
            var value = ReadValue(target, name);
            if (value == null)
            {
                continue;
            }
            try
            {
                return Convert.ToDouble(value);
            }
            catch (FormatException)
            {
            }
            catch (InvalidCastException)
            {
            }
        }
        return null;
    }

    private static string? ReadText(object target, string name)
    {
        return ReadValue(target, name)?.ToString();
    }

    private static object? ReadValue(object target, string name)
    {
        var flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.IgnoreCase;
        var type = target.GetType();
        var property = type.GetProperty(name, flags);
        if (property != null)
        {
            return property.GetValue(target);
        }
        var field = type.GetField(name, flags);
        return field?.GetValue(target);
    }

    private static bool CoversMonitor(Rectangle foreground, Rectangle monitor)
    {
        const int tolerance = 2;
        return foreground.Left <= monitor.Left + tolerance
            && foreground.Top <= monitor.Top + tolerance
            && foreground.Right >= monitor.Right - tolerance
            && foreground.Bottom >= monitor.Bottom - tolerance;
    }

    [DllImport("user32.dll")]
    private static extern IntPtr GetForegroundWindow();

    [DllImport("user32.dll")]
    private static extern bool GetWindowRect(IntPtr hWnd, out Rectangle lpRect);

    [DllImport("user32.dll")]
    private static extern IntPtr MonitorFromWindow(IntPtr hwnd, uint dwFlags);

    [DllImport("user32.dll", CharSet = CharSet.Auto)]
    private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MonitorInfo lpmi);

    private const uint MonitorDefaultToNearest = 2;

    [StructLayout(LayoutKind.Sequential)]
    private struct Rectangle
    {
        public int Left;
        public int Top;
        public int Right;
        public int Bottom;

        public int Width => Right - Left;
        public int Height => Bottom - Top;
    }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Auto)]
    private struct MonitorInfo
    {
        public uint CbSize;
        public Rectangle RcMonitor;
        public Rectangle RcWork;
        public uint DwFlags;
    }
}

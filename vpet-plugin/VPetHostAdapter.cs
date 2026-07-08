using System;

namespace MyBuddy.VPetPlugin;

public sealed class VPetHostAdapter : IVPetHost
{
    private readonly object _vpetPluginContext;

    public VPetHostAdapter(object vpetPluginContext)
    {
        _vpetPluginContext = vpetPluginContext;
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
            // TODO(vpet-api): 待 Windows 侧对 VPet 源码/VPet.Plugin.Demo 确认窗口拖拽/移动状态读取方式。
            return false;
        }
    }

    public void Attach()
    {
        // TODO(vpet-api): 待 Windows 侧确认 VPet 插件基类/生命周期入口,在插件 Load/Start 时调用本方法。
        // TODO(vpet-api): 待 Windows 侧确认 DLL/依赖包部署目录与插件 manifest/元数据格式。
        // TODO(vpet-api): 待 Windows 侧确认 Event_TouchHead/Event_TouchBody/投喂事件真实名称,在这里订阅并只 Raise*,不拦截原生处理。
        // TODO(vpet-api): 待 Windows 侧确认聊天输入/发送事件接法,在这里转发 ChatSubmitted。
        // TODO(vpet-api): 待 Windows 侧确认设置菜单/右键菜单挂载点,在这里转发 SettingsRequested。
    }

    public void Detach()
    {
        // TODO(vpet-api): 待 Windows 侧确认事件退订 API,避免插件卸载后残留委托。
    }

    public BodyState? CaptureBodyState()
    {
        // TODO(vpet-api): 待 Windows 侧确认 food/drink/feeling/health/strength/likability/money/mode 的读取属性名。
        return null;
    }

    public bool IsPresentationOrFullScreenActive()
    {
        // TODO(vpet-api): 待 Windows 侧确认 VPet 是否已有全屏/演示模式检测;没有则可在这里补 Win32 前台窗口矩形检测。
        return false;
    }

    public void PlayThinking()
    {
        // TODO(vpet-api): 待 Windows 侧确认 thinking 动作/动画 API,聊天发送瞬间调用用于延迟掩蔽。
    }

    public void ShowBubble(string text, bool interrupt = false)
    {
        // TODO(vpet-api): 待 Windows 侧确认 TalkBox/气泡 API;interrupt 只表示靠前展示,不要阻断 VPet 原生触摸台词。
    }

    public void ShowPersistentCard(string title, string text)
    {
        // TODO(vpet-api): 待 Windows 侧确认持久卡片或通知面板挂法,用于 overdue reminder,不打断桌宠。
    }

    public void MoveToForeground()
    {
        // TODO(vpet-api): 待 Windows 侧确认把桌宠移动到屏幕前/唤起窗口的 API。
    }

    public void ApplyAction(string actionName)
    {
        // TODO(vpet-api): 待 Windows 侧确认动作名到 VPet 动作枚举/方法的映射入口。
    }

    public void ApplyExpression(string expressionName)
    {
        // TODO(vpet-api): 待 Windows 侧确认表情名到 VPet 表情枚举/方法的映射入口。
    }

    public void ShowBridgeStatus(string text, BridgeStatusKind kind)
    {
        // TODO(vpet-api): 待 Windows 侧确认状态展示位置;token 错误必须可见,断网/超时只显示非阻塞状态。
    }

    public void RaiseTouchHead(TouchEventArgs e) => TouchHead?.Invoke(this, e);
    public void RaiseTouchBody(TouchEventArgs e) => TouchBody?.Invoke(this, e);
    public void RaiseFeed(FeedEventArgs e) => Feed?.Invoke(this, e);
    public void RaiseChatSubmitted(ChatSubmittedEventArgs e) => ChatSubmitted?.Invoke(this, e);
    public void RaiseSettingsRequested() => SettingsRequested?.Invoke(this, EventArgs.Empty);
}

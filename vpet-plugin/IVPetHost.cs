using System;
using System.Threading.Tasks;

namespace MyBuddy.VPetPlugin;

public interface IVPetHost
{
    event EventHandler<TouchEventArgs>? TouchHead;
    event EventHandler<TouchEventArgs>? TouchBody;
    event EventHandler<FeedEventArgs>? Feed;
    event EventHandler<ChatSubmittedEventArgs>? ChatSubmitted;
    event EventHandler? SettingsRequested;

    BodyState? CaptureBodyState();
    bool IsWindowDragInProgress { get; }
    bool IsPresentationOrFullScreenActive();

    void PlayThinking();
    void ShowBubble(string text, bool interrupt = false);
    void ShowPersistentCard(string title, string text);
    void MoveToForeground();
    void ApplyAction(string actionName);
    void ApplyExpression(string expressionName);
    void ShowBridgeStatus(string text, BridgeStatusKind kind);
}

public enum BridgeStatusKind
{
    Normal,
    Warning,
    Error,
}

public sealed class TouchEventArgs : EventArgs
{
    public TouchEventArgs(DateTimeOffset occurredAt, bool pointerMoved, TimeSpan pressDuration)
    {
        OccurredAt = occurredAt;
        PointerMoved = pointerMoved;
        PressDuration = pressDuration;
    }

    public DateTimeOffset OccurredAt { get; }
    public bool PointerMoved { get; }
    public TimeSpan PressDuration { get; }
}

public sealed class FeedEventArgs : EventArgs
{
    public FeedEventArgs(DateTimeOffset occurredAt, string? itemName)
    {
        OccurredAt = occurredAt;
        ItemName = itemName;
    }

    public DateTimeOffset OccurredAt { get; }
    public string? ItemName { get; }
}

public sealed class ChatSubmittedEventArgs : EventArgs
{
    public ChatSubmittedEventArgs(string message)
    {
        Message = message;
    }

    public string Message { get; }
}

public sealed class HostAction
{
    public HostAction(string actionName, string expressionName)
    {
        ActionName = actionName;
        ExpressionName = expressionName;
    }

    public string ActionName { get; }
    public string ExpressionName { get; }
}

public static class TaskExtensions
{
    public static async void ForgetWithStatus(this Task task, IVPetHost host)
    {
        try
        {
            await task.ConfigureAwait(false);
        }
        catch (BridgeRequestException e)
        {
            var kind = e.StatusCode == 401 ? BridgeStatusKind.Error : BridgeStatusKind.Warning;
            host.ShowBridgeStatus(e.Message, kind);
        }
        catch (Exception e)
        {
            host.ShowBridgeStatus(e.Message, BridgeStatusKind.Warning);
        }
    }
}

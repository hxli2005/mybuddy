using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Input;

namespace BuddyShell;

public sealed class SendRequestedEventArgs(string text) : EventArgs
{
    public string Text { get; } = text;
}

public partial class ChatPanel : UserControl
{
    private readonly ObservableCollection<string> _history = [];

    public ChatPanel()
    {
        InitializeComponent();
        History.ItemsSource = _history;
    }

    public event EventHandler<SendRequestedEventArgs>? SendRequested;

    public void AddUser(string text) => Add($"你:{text}");
    public void AddAssistant(string text) => Add($"小布:{text}");

    private void Add(string text)
    {
        _history.Add(text);
        while (_history.Count > 40) _history.RemoveAt(0);
        History.ScrollIntoView(_history[^1]);
    }

    private void Send_Click(object sender, RoutedEventArgs e) => Send();

    private void Input_KeyDown(object sender, KeyEventArgs e)
    {
        if (e.Key == Key.Enter && Keyboard.Modifiers == ModifierKeys.None)
        {
            Send();
            e.Handled = true;
        }
    }

    private void Send()
    {
        var text = Input.Text.Trim();
        if (text.Length == 0) return;
        Input.Clear();
        SendRequested?.Invoke(this, new SendRequestedEventArgs(text));
    }
}

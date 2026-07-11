using System.Collections.ObjectModel;
using System.Windows;
using System.Windows.Controls;
using System.Windows.Threading;

namespace BuddyShell;

public partial class Bubble : UserControl
{
    private readonly DispatcherTimer _hideTimer = new() { Interval = TimeSpan.FromSeconds(8) };
    private readonly ObservableCollection<string> _cards = [];

    public Bubble()
    {
        InitializeComponent();
        PersistentCards.ItemsSource = _cards;
        _hideTimer.Tick += (_, _) =>
        {
            _hideTimer.Stop();
            SpeechBorder.Visibility = Visibility.Collapsed;
        };
    }

    public void ShowSpeech(string text, bool interrupt)
    {
        if (string.IsNullOrWhiteSpace(text)) return;
        SpeechText.Text = text;
        SpeechBorder.Visibility = Visibility.Visible;
        _hideTimer.Stop();
        _hideTimer.Interval = TimeSpan.FromSeconds(interrupt ? 12 : 8);
        _hideTimer.Start();
    }

    public void ShowPersistent(string text)
    {
        if (!string.IsNullOrWhiteSpace(text) && !_cards.Contains(text)) _cards.Add(text);
    }
}

using VPet_Simulator.Windows.Interface;

namespace MyBuddy.VPetPlugin;

public sealed class MyBuddyTalkAPI : TalkBox
{
    private readonly VPetHostAdapter _adapter;

    public MyBuddyTalkAPI(MainPlugin plugin, VPetHostAdapter adapter) : base(plugin)
    {
        _adapter = adapter;
    }

    public override string APIName => "MyBuddy";

    public override void Responded(string content)
    {
        if (string.IsNullOrWhiteSpace(content))
        {
            return;
        }
        DisplayThink();
        _adapter.RaiseChatSubmitted(new ChatSubmittedEventArgs(content));
    }

    public override void Setting()
    {
        _adapter.RaiseSettingsRequested();
    }
}

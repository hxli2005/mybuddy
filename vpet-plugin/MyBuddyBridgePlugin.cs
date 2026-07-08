using System;
using System.IO;
using System.Text.Json;
using VPet_Simulator.Windows.Interface;

namespace MyBuddy.VPetPlugin;

public sealed class MyBuddyBridgePlugin : MainPlugin
{
    private readonly JsonSerializerOptions _json = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.CamelCase,
        WriteIndented = true,
    };

    private BridgeSettings _settings = new();
    private string? _settingsPath;
    private VPetHostAdapter? _adapter;
    private MyBuddyPlugin? _bridge;

    public MyBuddyBridgePlugin(IMainWindow mainwin) : base(mainwin)
    {
    }

    public override string PluginName => "MyBuddy Bridge";

    public override void LoadPlugin()
    {
        _settingsPath = Path.Combine(
            ExtensionValue.BaseDirectory,
            $"MyBuddyBridge{MW.PrefixSave}.json");
        _settings = LoadSettings(_settingsPath);
        _settings.NormalizeTodayQuiet();
        _adapter = new VPetHostAdapter(MW);
        _adapter.Attach();

        MW.TalkAPI.Add(new MyBuddyTalkAPI(this, _adapter));

        _bridge = new MyBuddyPlugin(_adapter, _settings);
        _bridge.Start();
    }

    public override void Save()
    {
        if (string.IsNullOrWhiteSpace(_settingsPath))
        {
            return;
        }
        File.WriteAllText(_settingsPath, JsonSerializer.Serialize(_settings, _json));
    }

    public override void EndGame()
    {
        _bridge?.Dispose();
        _bridge = null;
        _adapter?.Detach();
        _adapter = null;
        Save();
    }

    public override void Setting()
    {
        _adapter?.RaiseSettingsRequested();
    }

    private BridgeSettings LoadSettings(string path)
    {
        if (!File.Exists(path))
        {
            return new BridgeSettings();
        }
        try
        {
            var loaded = JsonSerializer.Deserialize<BridgeSettings>(
                File.ReadAllText(path),
                _json);
            return loaded ?? new BridgeSettings();
        }
        catch
        {
            return new BridgeSettings();
        }
    }
}

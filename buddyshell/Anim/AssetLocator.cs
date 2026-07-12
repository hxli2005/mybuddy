using Microsoft.Win32;
using System.IO;

namespace BuddyShell.Anim;

public static class AssetLocator
{
    public static string FindPetRoot()
    {
        var explicitRoot = Environment.GetEnvironmentVariable("BUDDYSHELL_PET_ROOT");
        var candidates = new List<string?>
        {
            explicitRoot,
            Path.Combine(AppContext.BaseDirectory, "assets", "pet"),
            Path.GetFullPath(Path.Combine(AppContext.BaseDirectory, "..", "..", "..", "assets", "pet")),
            FindSteamPetRoot(),
            @"D:\steam\steamapps\common\VPet\mod\0000_core\pet\vup",
        };
        var found = candidates.FirstOrDefault(path =>
            !string.IsNullOrWhiteSpace(path) && Directory.Exists(path));
        if (found is null)
        {
            throw new DirectoryNotFoundException(
                "找不到 VPet 素材。请设置 BUDDYSHELL_PET_ROOT 指向 0000_core/pet/vup。"
            );
        }
        return Path.GetFullPath(found);
    }

    public static string? FindFoodImage(string petRoot, string itemId)
    {
        var coreRoot = Directory.GetParent(Directory.GetParent(petRoot)?.FullName ?? "")?.FullName;
        if (string.IsNullOrWhiteSpace(coreRoot)) return null;
        var imageRoot = Path.Combine(coreRoot, "image", "food");
        var fileName = itemId switch
        {
            "congee" => "罗宋汤.png",
            "curry" => "番茄意面.png",
            "milk_tea" => "奶茶.png",
            "coffee" => "咖啡饮料.png",
            "water" => "矿泉水.png",
            _ => "矿泉水.png",
        };
        var path = Path.Combine(imageRoot, fileName);
        return File.Exists(path) ? path : null;
    }

    private static string? FindSteamPetRoot()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam");
            var steamPath = key?.GetValue("SteamPath")?.ToString();
            if (string.IsNullOrWhiteSpace(steamPath)) return null;
            return Path.Combine(
                steamPath,
                "steamapps",
                "common",
                "VPet",
                "mod",
                "0000_core",
                "pet",
                "vup");
        }
        catch
        {
            return null;
        }
    }
}

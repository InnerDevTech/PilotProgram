<#
Clicks a wireless display in the Windows Cast panel.

bridge/projector.py runs this after it presses Win+K. It is a separate file and
not a string inside the Python so you can run it by hand when a cast fails:

    powershell -ExecutionPolicy Bypass -File bridge/cast_click.ps1 `
        -Needle Hccast-5B8F5D_mira -Dump

Why it is written the way it is: the Cast panel is a XAML flyout owned by the
shell, and asking UI Automation for the desktop's children does not reliably
list it. So this finds the flyout as a plain Win32 window first, by class name,
and only then hands that window handle to UI Automation. Clicking is tried
three ways - Invoke, Select, and a real mouse click on the item - because which
of them a Windows build offers on these items keeps changing.

Prints CLICKED <name> and exits 0 on success, NOTFOUND and exits 1 otherwise.
With -Dump (or on failure) it also prints every shell window it looked in and
everything named inside them, which is what you need to see to fix a miss.
#>

param(
    [Parameter(Mandatory = $true)][string] $Needle,
    [string[]] $Tokens = @(),
    [int] $TimeoutSeconds = 12,
    [switch] $Dump
)

Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes

Add-Type @"
using System;
using System.Text;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class CastWin {
    delegate bool Cb(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(Cb e, IntPtr l);
    [DllImport("user32.dll")] static extern int GetClassName(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
    [DllImport("user32.dll")] public static extern bool GetCursorPos(out POINT p);

    [StructLayout(LayoutKind.Sequential)] public struct POINT { public int X, Y; }

    [StructLayout(LayoutKind.Sequential)]
    public struct MOUSEINPUT { public int dx, dy; public uint data, flags, time; public IntPtr extra; }
    [StructLayout(LayoutKind.Sequential)]
    public struct INPUT { public uint type; public MOUSEINPUT mi; }
    [DllImport("user32.dll")] static extern uint SendInput(uint n, INPUT[] i, int size);

    const uint LEFTDOWN = 0x0002, LEFTUP = 0x0004;

    // Every visible top-level window, as "handle|class".
    public static List<string> Windows() {
        var found = new List<string>();
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h)) return true;
            var cls = new StringBuilder(256);
            GetClassName(h, cls, 256);
            found.Add(h.ToInt64() + "|" + cls.ToString());
            return true;
        }, IntPtr.Zero);
        return found;
    }

    // A real left click where the cursor is, then put the cursor back.
    public static void Click(int x, int y) {
        POINT before;
        GetCursorPos(out before);
        SetCursorPos(x, y);
        var strokes = new INPUT[2];
        strokes[0].type = 0; strokes[0].mi.flags = LEFTDOWN;
        strokes[1].type = 0; strokes[1].mi.flags = LEFTUP;
        SendInput(2, strokes, Marshal.SizeOf(typeof(INPUT)));
        System.Threading.Thread.Sleep(150);
        SetCursorPos(before.X, before.Y);
    }
}
"@

# Windows that the Cast panel has lived in across Windows 10 and 11 builds.
# The device name is only ever matched inside one of these: the dongle's name
# is also on screen in a terminal or an editor that happens to be showing it,
# and clicking something in *that* window would click at random.
$shellClasses = @(
    'ControlCenterWindow',
    'XamlExplorerHostIslandWindow',
    'Windows.UI.Core.CoreWindow',
    'Shell_TrayWnd',
    'Shell_SecondaryTrayWnd',
    'TopLevelWindowForOverflowXamlIsland',
    'Windows.Internal.Shell.TabProxyWindow'
)

$walker = [Windows.Automation.TreeWalker]::ControlViewWalker
$needleLower = $Needle.ToLower()
$tokensLower = @(); foreach ($t in $Tokens) { if ($t) { $tokensLower += $t.ToLower() } }

function Get-ShellWindows {
    $windows = @()
    foreach ($entry in [CastWin]::Windows()) {
        $parts = $entry.Split('|')
        if ($parts.Count -lt 2) { continue }
        if ($shellClasses -notcontains $parts[1]) { continue }
        try {
            $element = [Windows.Automation.AutomationElement]::FromHandle([IntPtr][long]$parts[0])
        } catch { continue }
        if ($element) {
            $windows += [pscustomobject]@{ Class = $parts[1]; Element = $element }
        }
    }
    return $windows
}

function Get-Score($name) {
    # Higher is better. The panel labels a device with its own name, sometimes
    # with something appended ("... Connected"), so starts-with beats contains.
    if (-not $name) { return 0 }
    $lower = $name.ToLower()
    if ($lower -eq $needleLower) { return 4 }
    if ($lower.StartsWith($needleLower)) { return 3 }
    if ($lower.Contains($needleLower)) { return 2 }
    foreach ($token in $tokensLower) {
        # "Wireless Display Casting to Miracast" is the taskbar's own status
        # indicator, not a device - clicking it does nothing useful.
        if ($lower.Contains($token) -and $lower -notlike '*wireless display*') { return 1 }
    }
    return 0
}

function Invoke-Candidate($element) {
    try {
        $element.GetCurrentPattern([Windows.Automation.InvokePattern]::Pattern).Invoke()
        return $true
    } catch {}
    try {
        $element.GetCurrentPattern([Windows.Automation.SelectionItemPattern]::Pattern).Select()
        return $true
    } catch {}
    return $false
}

function Click-Candidate($element) {
    # Last resort: these XAML items sometimes expose no pattern at all, and a
    # click on the middle of the item is what a person would do anyway.
    try {
        $box = $element.Current.BoundingRectangle
        if ($box.Width -le 0 -or $box.Height -le 0) { return $false }
        [CastWin]::Click([int]($box.X + $box.Width / 2), [int]($box.Y + $box.Height / 2))
        return $true
    } catch { return $false }
}

function Write-Dump {
    Write-Output "--- cast panel dump ---"
    $windows = Get-ShellWindows
    if ($windows.Count -eq 0) {
        Write-Output "no shell windows found at all (is the Cast panel open?)"
    }
    foreach ($window in $windows) {
        Write-Output "window [$($window.Class)]"
        try {
            $all = $window.Element.FindAll(
                [Windows.Automation.TreeScope]::Descendants,
                [Windows.Automation.Condition]::TrueCondition)
        } catch { Write-Output "  (unreadable)"; continue }
        foreach ($e in $all) {
            $name = $e.Current.Name
            if (-not $name) { continue }
            $pats = @()
            foreach ($p in $e.GetSupportedPatterns()) {
                $pats += ($p.ProgrammaticName -replace 'PatternIdentifiers.Pattern', '')
            }
            $type = $e.Current.ControlType.ProgrammaticName -replace 'ControlType.', ''
            Write-Output "  $type pats=$($pats -join ',') name='$($name -replace "`n", ' / ')'"
        }
    }
    Write-Output "--- end dump ---"
}

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
while ((Get-Date) -lt $deadline) {
    $best = $null
    $bestScore = 0

    foreach ($window in Get-ShellWindows) {
        try {
            $all = $window.Element.FindAll(
                [Windows.Automation.TreeScope]::Descendants,
                [Windows.Automation.Condition]::TrueCondition)
        } catch { continue }

        foreach ($element in $all) {
            try { $name = $element.Current.Name } catch { continue }
            $score = Get-Score $name
            if ($score -gt $bestScore) {
                $bestScore = $score
                $best = $element
            }
        }
    }

    if ($best -ne $null) {
        $name = $best.Current.Name
        # The name usually sits on a Text node inside the clickable item, so
        # try the item itself and then its parents.
        $element = $best
        for ($level = 0; $level -lt 5 -and $element -ne $null; $level++) {
            if (Invoke-Candidate $element) {
                Write-Output "CLICKED $name"
                if ($Dump) { Write-Dump }
                exit 0
            }
            $element = $walker.GetParent($element)
        }
        if (Click-Candidate $best) {
            Write-Output "CLICKED $name"
            if ($Dump) { Write-Dump }
            exit 0
        }
    }

    Start-Sleep -Milliseconds 500
}

Write-Output "NOTFOUND"
Write-Dump
exit 1

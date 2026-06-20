<#
.SYNOPSIS
    GUI installer for a curated list of apps, using winget as the primary
    package manager and Chocolatey as an automatic backup.

.DESCRIPTION
    - Tries to use winget to install each app.
    - If winget itself is missing, the script attempts to install/repair it.
    - If winget still isn't available, or a specific app has no winget
      package, the script falls back to Chocolatey (installing Chocolatey
      first if needed).
    - Shows a simple WinForms GUI with a checklist, a Install button,
      a progress bar, and a live log.

.NOTES
    - Must be run on Windows (PowerShell 5.1+, which ships with Windows 10/11).
    - Will prompt for Administrator elevation (required for installing
      winget/Chocolatey and many app installers).
    - Run it by right-clicking the .ps1 file and choosing
      "Run with PowerShell", or from a PowerShell prompt:
          powershell -ExecutionPolicy Bypass -File .\install_apps.ps1
#>

# --------------------------------------------------------------------------
# Elevate to Administrator if needed
# --------------------------------------------------------------------------
$currentPrincipal = New-Object Security.Principal.WindowsPrincipal(
    [Security.Principal.WindowsIdentity]::GetCurrent()
)
if (-not $currentPrincipal.IsInRole([Security.Principal.WindowsBuiltinRole]::Administrator)) {
    $scriptPath = $MyInvocation.MyCommand.Path
    Start-Process -FilePath "powershell.exe" `
        -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "`"$scriptPath`"") `
        -Verb RunAs
    exit
}

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# --------------------------------------------------------------------------
# App catalog: Name, winget package ID, Chocolatey package ID
# (Set to $null when no package is known for that manager.)
# --------------------------------------------------------------------------
$Apps = @(
    [PSCustomObject]@{ Name = "File Shredder"; WingetId = $null;                       ChocoId = "fileshredder" }
    [PSCustomObject]@{ Name = "FreeTube";       WingetId = "PrestonN.FreeTube";         ChocoId = "freetube" }
    [PSCustomObject]@{ Name = "IVPN";           WingetId = "IVPN.IVPN";                 ChocoId = "ivpn" }
    [PSCustomObject]@{ Name = "Portmaster";     WingetId = "Safing.Portmaster";         ChocoId = "portmaster" }
    [PSCustomObject]@{ Name = "Discord";        WingetId = "Discord.Discord";           ChocoId = "discord" }
    [PSCustomObject]@{ Name = "Spotify";        WingetId = "Spotify.Spotify";           ChocoId = "spotify" }
    [PSCustomObject]@{ Name = "VSCodium";       WingetId = "VSCodium.VSCodium";         ChocoId = "vscodium" }
    [PSCustomObject]@{ Name = "Brave Browser";  WingetId = "Brave.Brave";               ChocoId = "brave" }
    [PSCustomObject]@{ Name = "Audacity";       WingetId = "Audacity.Audacity";         ChocoId = "audacity" }
    [PSCustomObject]@{ Name = "VLC";            WingetId = "VideoLAN.VLC";              ChocoId = "vlc" }
    [PSCustomObject]@{ Name = "Seafile Drive";  WingetId = "Seafile.Seadrive";          ChocoId = $null }
    [PSCustomObject]@{ Name = "Seafile Sync";   WingetId = "Seafile.Seafile";           ChocoId = "seafile-client" }
    [PSCustomObject]@{ Name = "EarTrumpet";     WingetId = "File-New-Project.EarTrumpet"; ChocoId = "eartrumpet" }
    [PSCustomObject]@{ Name = "NanaZip";        WingetId = "M2Team.NanaZip";            ChocoId = "nanazip" }
    [PSCustomObject]@{ Name = "Playnite";       WingetId = "Playnite.Playnite";         ChocoId = "playnite" }
    [PSCustomObject]@{ Name = "Steam";          WingetId = "Valve.Steam";               ChocoId = "steam" }
    [PSCustomObject]@{ Name = "Prism Launcher"; WingetId = "PrismLauncher.PrismLauncher"; ChocoId = "prismlauncher" }
    [PSCustomObject]@{ Name = "Telegram";       WingetId = "Telegram.TelegramDesktop";  ChocoId = "telegram" }
    [PSCustomObject]@{ Name = "Claude AI";      WingetId = "Anthropic.Claude";          ChocoId = $null }
    [PSCustomObject]@{ Name = "Cryptomator";    WingetId = "Cryptomator.Cryptomator";   ChocoId = "cryptomator" }
)

# --------------------------------------------------------------------------
# Worker script block. Runs on a background runspace so the GUI never
# freezes. Talks back to the GUI thread only through the synchronized
# hashtable ($Sync), which is thread-safe.
# --------------------------------------------------------------------------
$WorkerScript = {
    param($SelectedApps, $Sync)

    function Test-CommandExists {
        param([string]$Name)
        return [bool](Get-Command -Name $Name -ErrorAction SilentlyContinue)
    }

    function Refresh-Path {
        $machine = [System.Environment]::GetEnvironmentVariable("Path", "Machine")
        $user = [System.Environment]::GetEnvironmentVariable("Path", "User")
        $env:Path = "$machine;$user"
    }

    function Ensure-Winget {
        if (Test-CommandExists "winget") { return $true }

        $Sync.LogQueue.Enqueue("winget was not found. Attempting to install/repair it...")

        # First, try simply re-registering the App Installer package, in
        # case it's present but not on PATH / not registered for this user.
        try {
            $pkg = Get-AppxPackage -AllUsers -Name "Microsoft.DesktopAppInstaller" -ErrorAction SilentlyContinue
            if ($pkg) {
                Add-AppxPackage -DisableDevelopmentMode -Register "$($pkg.InstallLocation)\AppxManifest.xml" -ErrorAction SilentlyContinue
            }
        } catch {
            # ignore - we'll fall through to a full reinstall below
        }

        Refresh-Path
        if (Test-CommandExists "winget") {
            $Sync.LogQueue.Enqueue("winget was repaired successfully.")
            return $true
        }

        # Full reinstall: download the latest App Installer (winget) bundle
        # and its required dependencies directly from Microsoft/GitHub.
        try {
            $Sync.LogQueue.Enqueue("Downloading the latest winget (App Installer) package...")
            $tempDir = Join-Path $env:TEMP "winget_bootstrap"
            New-Item -ItemType Directory -Path $tempDir -Force | Out-Null

            $vclibsUrl = "https://aka.ms/Microsoft.VCLibs.x64.14.00.Desktop.appx"
            $vclibsFile = Join-Path $tempDir "VCLibs.x64.appx"
            Invoke-WebRequest -Uri $vclibsUrl -OutFile $vclibsFile -UseBasicParsing

            $xamlUrl = "https://github.com/microsoft/microsoft-ui-xaml/releases/download/v2.8.6/Microsoft.UI.Xaml.2.8.x64.appx"
            $xamlFile = Join-Path $tempDir "UiXaml.x64.appx"
            Invoke-WebRequest -Uri $xamlUrl -OutFile $xamlFile -UseBasicParsing

            $release = Invoke-RestMethod -Uri "https://api.github.com/repos/microsoft/winget-cli/releases/latest" -UseBasicParsing
            $asset = $release.assets | Where-Object { $_.name -like "*.msixbundle" } | Select-Object -First 1
            if (-not $asset) {
                throw "Could not find a winget .msixbundle in the latest GitHub release."
            }
            $bundleFile = Join-Path $tempDir $asset.name
            Invoke-WebRequest -Uri $asset.browser_download_url -OutFile $bundleFile -UseBasicParsing

            Add-AppxPackage -Path $vclibsFile -ErrorAction SilentlyContinue
            Add-AppxPackage -Path $xamlFile -ErrorAction SilentlyContinue
            Add-AppxPackage -Path $bundleFile -ErrorAction Stop

            $Sync.LogQueue.Enqueue("winget installed successfully.")
        } catch {
            $Sync.LogQueue.Enqueue("Could not install winget automatically: $($_.Exception.Message)")
        }

        Refresh-Path
        return (Test-CommandExists "winget")
    }

    function Ensure-Choco {
        if (Test-CommandExists "choco") { return $true }

        $Sync.LogQueue.Enqueue("Chocolatey was not found. Installing it as the backup package manager...")
        try {
            Set-ExecutionPolicy Bypass -Scope Process -Force
            [System.Net.ServicePointManager]::SecurityProtocol = `
                [System.Net.ServicePointManager]::SecurityProtocol -bor 3072
            $installScript = (New-Object System.Net.WebClient).DownloadString("https://community.chocolatey.org/install.ps1")
            Invoke-Expression $installScript
            Refresh-Path
            $Sync.LogQueue.Enqueue("Chocolatey installed successfully.")
        } catch {
            $Sync.LogQueue.Enqueue("Could not install Chocolatey automatically: $($_.Exception.Message)")
        }

        return (Test-CommandExists "choco")
    }

    function Run-Installer {
        param([string]$FilePath, [string[]]$ArgList)

        $stdOut = [System.IO.Path]::GetTempFileName()
        $stdErr = [System.IO.Path]::GetTempFileName()
        try {
            $proc = Start-Process -FilePath $FilePath -ArgumentList $ArgList `
                -NoNewWindow -Wait -PassThru `
                -RedirectStandardOutput $stdOut -RedirectStandardError $stdErr
            $tailOut = (Get-Content $stdOut -Tail 5 -ErrorAction SilentlyContinue) -join " | "
            $tailErr = (Get-Content $stdErr -Tail 5 -ErrorAction SilentlyContinue) -join " | "
            return [PSCustomObject]@{
                ExitCode = $proc.ExitCode
                Tail     = if ($tailErr) { $tailErr } else { $tailOut }
            }
        } finally {
            Remove-Item $stdOut, $stdErr -ErrorAction SilentlyContinue
        }
    }

    $wingetAvailable = Ensure-Winget
    $chocoAvailable = $null   # decided lazily, only the first time it's actually needed

    $Sync.Total = $SelectedApps.Count
    $Sync.Current = 0

    foreach ($app in $SelectedApps) {
        $Sync.Current++
        $installed = $false

        if ($app.WingetId -and $wingetAvailable) {
            $Sync.LogQueue.Enqueue("[$($Sync.Current)/$($Sync.Total)] $($app.Name): trying winget ($($app.WingetId))...")
            $result = Run-Installer -FilePath "winget" -ArgList @(
                "install", "--id", $app.WingetId, "-e", "--silent",
                "--accept-package-agreements", "--accept-source-agreements"
            )
            if ($result.ExitCode -eq 0) {
                $installed = $true
                $Sync.LogQueue.Enqueue("    -> installed via winget.")
            } else {
                $Sync.LogQueue.Enqueue("    -> winget failed (exit $($result.ExitCode)). $($result.Tail)")
            }
        } elseif (-not $app.WingetId) {
            $Sync.LogQueue.Enqueue("[$($Sync.Current)/$($Sync.Total)] $($app.Name): no winget package known, trying Chocolatey...")
        } else {
            $Sync.LogQueue.Enqueue("[$($Sync.Current)/$($Sync.Total)] $($app.Name): winget unavailable, trying Chocolatey...")
        }

        if (-not $installed -and $app.ChocoId) {
            if ($null -eq $chocoAvailable) { $chocoAvailable = Ensure-Choco }
            if ($chocoAvailable) {
                $Sync.LogQueue.Enqueue("    Trying Chocolatey ($($app.ChocoId))...")
                $result = Run-Installer -FilePath "choco" -ArgList @("install", $app.ChocoId, "-y", "--no-progress")
                if ($result.ExitCode -eq 0) {
                    $installed = $true
                    $Sync.LogQueue.Enqueue("    -> installed via Chocolatey.")
                } else {
                    $Sync.LogQueue.Enqueue("    -> Chocolatey failed too (exit $($result.ExitCode)). $($result.Tail)")
                }
            } else {
                $Sync.LogQueue.Enqueue("    -> Chocolatey is unavailable; cannot install $($app.Name).")
            }
        } elseif (-not $installed -and -not $app.ChocoId) {
            $Sync.LogQueue.Enqueue("    -> No Chocolatey package known either. Please install $($app.Name) manually.")
        }

        if (-not $installed) {
            $Sync.LogQueue.Enqueue("  ** FAILED: $($app.Name) **")
        }
    }

    $Sync.LogQueue.Enqueue("===== All selected apps processed. =====")
    $Sync.Done = $true
}

# --------------------------------------------------------------------------
# Build the GUI
# --------------------------------------------------------------------------
$form = New-Object System.Windows.Forms.Form
$form.Text = "App Installer (winget + Chocolatey backup)"
$form.Size = New-Object System.Drawing.Size(560, 640)
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$label = New-Object System.Windows.Forms.Label
$label.Text = "Select the apps to install:"
$label.Location = New-Object System.Drawing.Point(15, 15)
$label.AutoSize = $true
$form.Controls.Add($label)

$checklist = New-Object System.Windows.Forms.CheckedListBox
$checklist.Location = New-Object System.Drawing.Point(15, 40)
$checklist.Size = New-Object System.Drawing.Size(515, 260)
$checklist.CheckOnClick = $true
foreach ($app in $Apps) {
    [void]$checklist.Items.Add($app.Name, $true)
}
$form.Controls.Add($checklist)

$selectAllBtn = New-Object System.Windows.Forms.Button
$selectAllBtn.Text = "Select All"
$selectAllBtn.Location = New-Object System.Drawing.Point(15, 305)
$selectAllBtn.Size = New-Object System.Drawing.Size(100, 28)
$form.Controls.Add($selectAllBtn)

$selectNoneBtn = New-Object System.Windows.Forms.Button
$selectNoneBtn.Text = "Select None"
$selectNoneBtn.Location = New-Object System.Drawing.Point(125, 305)
$selectNoneBtn.Size = New-Object System.Drawing.Size(100, 28)
$form.Controls.Add($selectNoneBtn)

$installBtn = New-Object System.Windows.Forms.Button
$installBtn.Text = "Install Selected"
$installBtn.Location = New-Object System.Drawing.Point(345, 305)
$installBtn.Size = New-Object System.Drawing.Size(185, 32)
$installBtn.Font = New-Object System.Drawing.Font($installBtn.Font, [System.Drawing.FontStyle]::Bold)
$form.Controls.Add($installBtn)

$progressBar = New-Object System.Windows.Forms.ProgressBar
$progressBar.Location = New-Object System.Drawing.Point(15, 345)
$progressBar.Size = New-Object System.Drawing.Size(515, 20)
$progressBar.Minimum = 0
$form.Controls.Add($progressBar)

$statusLabel = New-Object System.Windows.Forms.Label
$statusLabel.Text = "Ready."
$statusLabel.Location = New-Object System.Drawing.Point(15, 370)
$statusLabel.AutoSize = $true
$form.Controls.Add($statusLabel)

$logLabel = New-Object System.Windows.Forms.Label
$logLabel.Text = "Log:"
$logLabel.Location = New-Object System.Drawing.Point(15, 395)
$logLabel.AutoSize = $true
$form.Controls.Add($logLabel)

$logBox = New-Object System.Windows.Forms.TextBox
$logBox.Location = New-Object System.Drawing.Point(15, 415)
$logBox.Size = New-Object System.Drawing.Size(515, 175)
$logBox.Multiline = $true
$logBox.ScrollBars = "Vertical"
$logBox.ReadOnly = $true
$logBox.Font = New-Object System.Drawing.Font("Consolas", 9)
$form.Controls.Add($logBox)

function Append-Log {
    param([string]$Text)
    $logBox.AppendText("$Text`r`n")
}

$selectAllBtn.Add_Click({
    for ($i = 0; $i -lt $checklist.Items.Count; $i++) {
        $checklist.SetItemChecked($i, $true)
    }
})

$selectNoneBtn.Add_Click({
    for ($i = 0; $i -lt $checklist.Items.Count; $i++) {
        $checklist.SetItemChecked($i, $false)
    }
})

# --------------------------------------------------------------------------
# Install button: kick off the background runspace and a timer that
# drains its log queue into the GUI without blocking the UI thread.
# --------------------------------------------------------------------------
$script:Sync = $null
$script:PS = $null
$script:Runspace = $null
$script:Timer = $null

$installBtn.Add_Click({
    $selectedNames = $checklist.CheckedItems | ForEach-Object { $_.ToString() }
    if ($selectedNames.Count -eq 0) {
        [System.Windows.Forms.MessageBox]::Show("Select at least one app to install.", "Nothing selected") | Out-Null
        return
    }
    $selectedApps = $Apps | Where-Object { $selectedNames -contains $_.Name }

    $installBtn.Enabled = $false
    $selectAllBtn.Enabled = $false
    $selectNoneBtn.Enabled = $false
    $checklist.Enabled = $false
    $logBox.Clear()
    $progressBar.Value = 0
    $progressBar.Maximum = [Math]::Max($selectedApps.Count, 1)
    $statusLabel.Text = "Installing..."

    $script:Sync = [hashtable]::Synchronized(@{})
    $script:Sync.LogQueue = [System.Collections.Concurrent.ConcurrentQueue[string]]::new()
    $script:Sync.Done = $false
    $script:Sync.Total = $selectedApps.Count
    $script:Sync.Current = 0

    $script:Runspace = [runspacefactory]::CreateRunspace()
    $script:Runspace.Open()
    $script:PS = [powershell]::Create()
    $script:PS.Runspace = $script:Runspace
    [void]$script:PS.AddScript($WorkerScript).AddArgument($selectedApps).AddArgument($script:Sync)
    $script:AsyncHandle = $script:PS.BeginInvoke()

    $script:Timer = New-Object System.Windows.Forms.Timer
    $script:Timer.Interval = 250
    $script:Timer.Add_Tick({
        $line = $null
        while ($script:Sync.LogQueue.TryDequeue([ref]$line)) {
            Append-Log $line
        }
        $progressBar.Value = [Math]::Min($script:Sync.Current, $progressBar.Maximum)
        $statusLabel.Text = "Installing $($script:Sync.Current) of $($script:Sync.Total)..."

        if ($script:Sync.Done) {
            $script:Timer.Stop()
            $statusLabel.Text = "Finished. See log for results."
            try { $script:PS.EndInvoke($script:AsyncHandle) } catch {}
            $script:PS.Dispose()
            $script:Runspace.Close()
            $script:Runspace.Dispose()
            $installBtn.Enabled = $true
            $selectAllBtn.Enabled = $true
            $selectNoneBtn.Enabled = $true
            $checklist.Enabled = $true
            [System.Windows.Forms.MessageBox]::Show("Done. Check the log for any failures.", "Install complete") | Out-Null
        }
    })
    $script:Timer.Start()
})

$form.Add_FormClosing({
    if ($script:Timer) { $script:Timer.Stop() }
    if ($script:PS) { try { $script:PS.Stop() } catch {} }
    if ($script:Runspace) { try { $script:Runspace.Close() } catch {} }
})

[void]$form.ShowDialog()

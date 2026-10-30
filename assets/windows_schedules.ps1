param([Parameter(Mandatory=$true)][string]$InputPath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false

try {
    $taskRequest = Get-Content -LiteralPath $InputPath -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($taskRequest.owner -notmatch '^[0-9a-f]{32}$' -or $taskRequest.folder -cne ('\AutoZZZ\' + $taskRequest.owner)) { throw '计划目录无效。' }
    if ($taskRequest.operation -notin @('list','register','enable','disable','remove','run','instances','remove_empty_folder')) { throw '计划操作无效。' }
    if ($taskRequest.operation -notin @('list','remove_empty_folder') -and $taskRequest.key -notmatch '^[0-9a-f]{32}$') { throw '计划编号无效。' }
    $taskService = New-Object -ComObject Schedule.Service
    $taskService.Connect()
    $taskSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value

    function Get-TaskFolder([bool]$Create) {
        try { return $taskService.GetFolder($taskRequest.folder) }
        catch {
            if ($_.Exception.HResult -ne -2147024894) { throw }
            if (-not $Create) { return $null }
        }
        $taskRootFolder = $taskService.GetFolder('\')
        try { $taskParentFolder = $taskService.GetFolder('\AutoZZZ') }
        catch {
            if ($_.Exception.HResult -ne -2147024894) { throw }
            $taskParentFolder = $taskRootFolder.CreateFolder('AutoZZZ', $null)
        }
        return $taskParentFolder.CreateFolder($taskRequest.owner, $null)
    }

    function Assert-TaskOwner($Entry) {
        if ($Entry.Name -notmatch '^[0-9a-f]{32}$') { throw '任务名称不属于 AutoZZZ 计划。' }
        $taskDefinition = $Entry.Definition
        if ($taskDefinition.RegistrationInfo.Source -cne ('AutoZZZ/' + $taskRequest.owner) -or $taskDefinition.Actions.Count -ne 1) { throw '系统任务已由外部修改，未覆盖。' }
        $taskAction = $taskDefinition.Actions.Item(1)
        $taskPrincipalSid = $taskDefinition.Principal.UserId
        if ($taskPrincipalSid -notmatch '^S-1-') {
            $taskPrincipalSid = ([System.Security.Principal.NTAccount]::new($taskPrincipalSid)).Translate([System.Security.Principal.SecurityIdentifier]).Value
        }
        if ($taskAction.Id -cne 'autozzz' -or $taskAction.Type -ne 0 -or $taskAction.Path -ine $taskRequest.command -or $taskAction.WorkingDirectory -ine $taskRequest.working -or $taskPrincipalSid -ine $taskSid) {
            throw '系统任务的用户、启动程序或目录已变化，未覆盖。'
        }
    }

    function Read-Task($Entry) {
        $taskConflict = ''
        try { Assert-TaskOwner $Entry } catch { $taskConflict = $_.Exception.Message }
        return [pscustomobject]@{ id=$Entry.Name; enabled=[bool]$Entry.Enabled; state=[int]$Entry.State; next=$Entry.NextRunTime.ToString('o'); last=$Entry.LastRunTime.ToString('o'); result=[long]$Entry.LastTaskResult; conflict=$taskConflict; xml=$Entry.Xml }
    }

    $taskFolder = Get-TaskFolder ($taskRequest.operation -eq 'register')
    if ($taskRequest.operation -eq 'list') {
        $taskRows = @()
        if ($taskFolder) { foreach ($taskEntry in $taskFolder.GetTasks(1)) { $taskRows += Read-Task $taskEntry } }
        @{ ok=$true; tasks=@($taskRows); folder=$taskRequest.folder } | ConvertTo-Json -Depth 8 -Compress
        exit 0
    }
    if ($taskRequest.operation -eq 'remove_empty_folder') {
        if ($taskFolder) {
            if ($taskFolder.GetTasks(1).Count -or $taskFolder.GetFolders(0).Count) { throw '计划目录仍有任务，未移除。' }
            $taskService.GetFolder('\AutoZZZ').DeleteFolder($taskRequest.owner,0)
        }
        @{ ok=$true } | ConvertTo-Json -Compress
        exit 0
    }
    if (-not $taskFolder) { throw '系统计划不存在。' }
    $taskEntry = $null
    try { $taskEntry = $taskFolder.GetTask($taskRequest.key) }
    catch { if ($_.Exception.HResult -ne -2147024894) { throw } }
    if ($taskEntry) { Assert-TaskOwner $taskEntry }

    if ($taskRequest.operation -eq 'register') {
        if ($taskEntry -and $taskEntry.GetInstances(0).Count) { throw '原计划仍在运行，请正常停止并等待退出后再修改。' }
        [xml]$taskXml = $taskRequest.xml
        $taskNs = New-Object System.Xml.XmlNamespaceManager($taskXml.NameTable)
        $taskNs.AddNamespace('t','http://schemas.microsoft.com/windows/2004/02/mit/task')
        if ($taskXml.SelectSingleNode('/t:Task/t:RegistrationInfo/t:Source',$taskNs).InnerText -cne ('AutoZZZ/' + $taskRequest.owner)) { throw '任务来源无效。' }
        foreach ($taskUserNode in $taskXml.SelectNodes('//t:UserId',$taskNs)) {
            if ($taskUserNode.InnerText -cne 'CURRENT_USER_SID') { throw '只能为当前用户建立计划。' }
            $taskUserNode.InnerText = $taskSid
        }
        $taskNewDefinition = $taskService.NewTask(0)
        $taskNewDefinition.XmlText = $taskXml.OuterXml
        if ($taskNewDefinition.Actions.Count -ne 1 -or $taskNewDefinition.Actions.Item(1).Path -ine $taskRequest.command -or $taskNewDefinition.Actions.Item(1).WorkingDirectory -ine $taskRequest.working -or $taskNewDefinition.Principal.LogonType -ne 3) { throw '任务启动信息无效。' }
        $taskEntry = $taskFolder.RegisterTaskDefinition($taskRequest.key,$taskNewDefinition,6,$taskSid,$null,3,$null)
        Assert-TaskOwner $taskEntry
        @{ ok=$true; task=(Read-Task $taskEntry) } | ConvertTo-Json -Depth 8 -Compress
    } else {
        if (-not $taskEntry) { throw '系统计划不存在。' }
        switch ($taskRequest.operation) {
            'enable' { $taskEntry.Enabled = $true }
            'disable' { $taskEntry.Enabled = $false }
            'remove' {
                if ($taskEntry.GetInstances(0).Count) { throw '原计划仍在运行，请正常停止并等待退出后再移除。' }
                $taskFolder.DeleteTask($taskRequest.key,0)
            }
            'run' {
                if (-not $taskEntry.Enabled) { throw '请先启用计划。' }
                $taskRunning = $taskEntry.Run($null)
                @{ ok=$true; instance=$taskRunning.InstanceGuid; state=[int]$taskRunning.State } | ConvertTo-Json -Compress
                exit 0
            }
            'instances' {
                $taskInstances = @()
                foreach ($taskRunning in $taskEntry.GetInstances(0)) { $taskInstances += [pscustomobject]@{ instance=$taskRunning.InstanceGuid; state=[int]$taskRunning.State; engine_pid=[long]$taskRunning.EnginePID } }
                @{ ok=$true; instances=@($taskInstances) } | ConvertTo-Json -Depth 8 -Compress
                exit 0
            }
        }
        @{ ok=$true } | ConvertTo-Json -Compress
    }
} catch {
    @{ ok=$false; error=$_.Exception.Message } | ConvertTo-Json -Compress
    exit 1
}

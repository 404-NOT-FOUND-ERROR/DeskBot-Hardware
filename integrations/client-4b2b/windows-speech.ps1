param([Parameter(Mandatory=$true)][string]$RequestPath,
      [Parameter(Mandatory=$true)][string]$ResponsePath)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Speech
$request = [System.IO.File]::ReadAllText($RequestPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$result = $null
if ($request.action -eq 'synthesize') {
    $synth = New-Object System.Speech.Synthesis.SpeechSynthesizer
    try {
        $voiceName = if ([string]::IsNullOrWhiteSpace([string]$request.voice_name)) {
            'Microsoft Huihui Desktop'
        } else { [string]$request.voice_name }
        $installedNames = @($synth.GetInstalledVoices() | Where-Object { $_.Enabled } | ForEach-Object { $_.VoiceInfo.Name })
        if ($installedNames -notcontains $voiceName) { throw 'requested_voice_unavailable' }
        $synth.SelectVoice($voiceName)
        $format = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(
            [int]$request.sample_rate,
            [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen,
            [System.Speech.AudioFormat.AudioChannel]::Mono)
        $synth.SetOutputToWaveFile([string]$request.output, $format)
        $synth.Speak([string]$request.text)
        $synth.SetOutputToNull()
        $result = @{provider='windows-system-speech'; voice=$synth.Voice.Name; sample_rate=$request.sample_rate}
    } finally { $synth.Dispose() }
} elseif ($request.action -eq 'recognize') {
    $info = [System.Speech.Recognition.SpeechRecognitionEngine]::InstalledRecognizers() |
        Where-Object { $_.Culture.Name -eq 'zh-CN' } | Select-Object -First 1
    if (-not $info) { throw 'chinese_recognizer_unavailable' }
    # SAPI can emit multiple phrases within a single endpointed capture. Collect
    # results until its WAV end-of-stream, including pauses inside a sentence.
    Add-Type -ReferencedAssemblies ([System.Speech.Recognition.SpeechRecognitionEngine].Assembly.Location) -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Speech.Recognition;
using System.Threading;
public class DeskBotSpeechSegment {
    public string text;
    public float confidence;
}
public static class DeskBotWaveRecognizer {
    public static DeskBotSpeechSegment[] Read(string path, RecognizerInfo info) {
        using (var engine = new SpeechRecognitionEngine(info))
        using (var done = new ManualResetEvent(false)) {
            var results = new List<DeskBotSpeechSegment>();
            Exception failed = null;
            engine.LoadGrammar(new DictationGrammar());
            engine.SpeechRecognized += (sender, args) => {
                lock(results) {
                    if(results.Count < 30) results.Add(new DeskBotSpeechSegment {
                        text = args.Result.Text, confidence = args.Result.Confidence });
                }
            };
            engine.RecognizeCompleted += (sender, args) => { failed = args.Error; done.Set(); };
            engine.SetInputToWaveFile(path);
            engine.RecognizeAsync(RecognizeMode.Multiple);
            if(!done.WaitOne(35000)) { engine.RecognizeAsyncCancel(); done.WaitOne(1000); throw new TimeoutException(); }
            if(failed != null) throw failed;
            lock(results) { return results.ToArray(); }
        }
    }
}
'@
    $segments = @([DeskBotWaveRecognizer]::Read([string]$request.input, $info))
    $text = ($segments | ForEach-Object { $_.text }) -join ''
    $result = @{provider='windows-system-speech'; recognizer=$info.Id; culture='zh-CN';
        raw_utterance=$text; clean_utterance=$text.Trim(); segments=@($segments)}
} else { throw 'unsupported_action' }
[System.IO.File]::WriteAllText($ResponsePath, ($result | ConvertTo-Json -Depth 8 -Compress),
    (New-Object System.Text.UTF8Encoding($false)))

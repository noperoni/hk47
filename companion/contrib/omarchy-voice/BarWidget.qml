import QtQuick
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

// The overseer's stopper (PERS-5) on the Omarchy bar. Lit while the droid may
// speak the queue; a click silences him mid-sentence and keeps the queue in
// writing until clicked again. Install by symlinking this directory to
// ~/.config/omarchy/plugins/hk47.voice and adding {"id": "hk47.voice"} to the
// bar in ~/.config/omarchy/shell.json.
BarWidget {
  id: root
  moduleName: "hk47.voice"

  readonly property string script: Quickshell.env("HOME") + "/.config/omarchy/plugins/hk47.voice/bin/hk47-voice"
  property string icon: "?"
  property string tooltip: "HK-47 voice: checking…"
  property bool voiceOn: true

  function refresh() {
    if (!statusProc.running) statusProc.running = true
  }

  implicitWidth: button.implicitWidth
  implicitHeight: button.implicitHeight

  // hk47-overseer.py voice pushes a refresh here, so a change made from the
  // terminal shows at once rather than at the next poll.
  IpcHandler {
    target: "hk47.voice"

    function refresh(): void {
      root.broadcast("refresh")
    }
  }

  Process {
    id: statusProc
    command: [root.script, "status"]
    stdout: StdioCollector {
      waitForEnd: true
      onStreamFinished: {
        var raw = String(text || "").trim()
        if (!raw) return
        try {
          var data = JSON.parse(raw)
          root.icon = String(data.text || "")
          root.tooltip = String(data.tooltip || "")
          root.voiceOn = String(data["class"] || "") === "on"
        } catch (e) {
          console.log("hk47.voice: unparsable status line: " + raw)
        }
      }
    }
  }

  Process {
    id: toggleProc
    command: [root.script, "toggle"]
    onExited: root.refresh()
  }

  Timer {
    interval: 30000
    running: true
    repeat: true
    triggeredOnStart: true
    onTriggered: root.refresh()
  }

  BarIconButton {
    id: button
    anchors.fill: parent
    bar: root.bar
    text: root.icon
    slotSize: Style.bar.statusSlot
    fontSize: Style.font.caption
    tooltipText: root.tooltip
    active: root.voiceOn
    onPressed: if (!toggleProc.running) toggleProc.running = true
  }
}

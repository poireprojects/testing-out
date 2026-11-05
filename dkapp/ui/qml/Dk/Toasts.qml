import QtQuick
import QtQuick.Layouts

// In-app notification stack (top-right). Native OS notifications are sent separately.
Item {
    id: root
    width: 360
    function show(kind, title, message) {
        toastModel.insert(0, { kind: kind, title: title, message: message, uid: Math.random() })
        if (toastModel.count > 4) toastModel.remove(toastModel.count - 1)
    }
    ListModel { id: toastModel }
    ListView {
        anchors.fill: parent
        model: toastModel
        spacing: 10
        interactive: false
        add: Transition {
            ParallelAnimation {
                NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 220 }
                NumberAnimation { property: "x"; from: 40; to: 0; duration: 280; easing.type: Easing.OutCubic }
            }
        }
        remove: Transition { NumberAnimation { property: "opacity"; to: 0; duration: 180 } }
        displaced: Transition { NumberAnimation { properties: "y"; duration: 220; easing.type: Easing.OutCubic } }
        delegate: Rectangle {
            id: card
            width: 360
            height: col.implicitHeight + 26
            radius: 16
            color: Theme.surface2
            border.color: kind === "error" ? Theme.coral : kind === "warning" ? Theme.amber
                        : kind === "success" ? Theme.lime : Theme.borderStrong
            Timer { interval: kind === "error" ? 9000 : 5500; running: true; onTriggered: toastModel.remove(index) }
            RowLayout {
                anchors.fill: parent
                anchors.margins: 13
                spacing: 12
                Rectangle {
                    Layout.alignment: Qt.AlignTop
                    width: 30; height: 30; radius: 15
                    color: kind === "error" ? Theme.coralSoft : kind === "warning" ? Theme.amberSoft
                         : kind === "success" ? Theme.limeSoft : Theme.slateSoft
                    Icon {
                        anchors.centerIn: parent; size: 16
                        name: kind === "error" ? "alert" : kind === "warning" ? "alert" : kind === "success" ? "check" : "sparkle"
                        color: kind === "error" ? Theme.coral : kind === "warning" ? Theme.amber : kind === "success" ? Theme.lime : Theme.textDim
                    }
                }
                ColumnLayout {
                    id: col
                    Layout.fillWidth: true
                    spacing: 3
                    Txt { text: title; font.weight: Font.DemiBold; font.pixelSize: 14; Layout.fillWidth: true }
                    Txt { text: message; visible: message !== ""; color: Theme.textDim; font.pixelSize: 13; wrapMode: Text.WordWrap; maximumLineCount: 3; Layout.fillWidth: true }
                }
            }
            MouseArea { anchors.fill: parent; onClicked: toastModel.remove(index) }
        }
    }
}

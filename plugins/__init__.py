from plugins.direct_file_send import DirectFileSendPlugin
from plugins.direct_image_send import DirectImageSendPlugin
from plugins.image_analyze import ImageAnalyzePlugin
from plugins.image_to_ppt import ImageToPptPlugin
from plugins.normal_chat import NormalChatPlugin
from plugins.self_maintenance import SelfMaintenancePlugin


def default_plugins():
    return [
        SelfMaintenancePlugin(),
        DirectFileSendPlugin(),
        DirectImageSendPlugin(),
        ImageToPptPlugin(),
        ImageAnalyzePlugin(),
        NormalChatPlugin(),
    ]


__all__ = [
    "DirectFileSendPlugin",
    "DirectImageSendPlugin",
    "ImageAnalyzePlugin",
    "ImageToPptPlugin",
    "NormalChatPlugin",
    "SelfMaintenancePlugin",
    "default_plugins",
]

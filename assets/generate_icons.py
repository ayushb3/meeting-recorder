"""Regenerate menu bar icons from SF Symbols using AppKit. Run with the project venv."""
import os
import AppKit

def render_sf_symbol(symbol_name, path, size=22):
    cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(
        size, AppKit.NSFontWeightRegular
    )
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
        symbol_name, None
    )
    img = img.imageWithSymbolConfiguration_(cfg)
    tiff = img.TIFFRepresentation()
    bitmap = AppKit.NSBitmapImageRep.imageRepWithData_(tiff)
    png_data = bitmap.representationUsingType_properties_(
        AppKit.NSBitmapImageFileTypePNG, {}
    )
    png_data.writeToFile_atomically_(path, True)
    print(f"Saved {path}")

os.makedirs(os.path.dirname(__file__), exist_ok=True)
render_sf_symbol("mic",      os.path.join(os.path.dirname(__file__), "icon.png"))
render_sf_symbol("mic.fill", os.path.join(os.path.dirname(__file__), "icon-recording.png"))

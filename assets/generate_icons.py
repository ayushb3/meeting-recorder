"""Regenerate menu bar icons from SF Symbols using AppKit. Run with the project venv."""
import os
import AppKit

def render_sf_symbol(symbol_name, path, size=22, weight=AppKit.NSFontWeightRegular):
    cfg = AppKit.NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weight)
    img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol_name, None)
    img = img.imageWithSymbolConfiguration_(cfg)
    tiff = img.TIFFRepresentation()
    bitmap = AppKit.NSBitmapImageRep.imageRepWithData_(tiff)
    png_data = bitmap.representationUsingType_properties_(AppKit.NSBitmapImageFileTypePNG, {})
    png_data.writeToFile_atomically_(path, True)
    print(f"Saved {path}")

base = os.path.dirname(__file__)
render_sf_symbol("waveform.and.mic", os.path.join(base, "icon.png"),           weight=AppKit.NSFontWeightLight)
render_sf_symbol("waveform.and.mic", os.path.join(base, "icon-recording.png"), weight=AppKit.NSFontWeightBold)

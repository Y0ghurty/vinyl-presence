"""Writes the Windows version resource for the exe (product name, version) for PyInstaller's --version-file.

    python .github/version_info.py 1.13.0 > version_info.txt
"""
import sys

version = sys.argv[1]
parts = tuple(int(p) for p in (version.split(".") + ["0", "0", "0"])[:4])
print(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={parts}, prodvers={parts}, mask=0x3f, flags=0x0, OS=0x40004, fileType=0x1,
                    subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'Vinyl Presence'),
      StringStruct('FileDescription', 'Vinyl Presence'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'VinylPresence'),
      StringStruct('LegalCopyright', 'CC BY-NC-ND 4.0'),
      StringStruct('OriginalFilename', 'VinylPresence.exe'),
      StringStruct('ProductName', 'Vinyl Presence'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)""")

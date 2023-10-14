import sys
sys.path.append("./")
import sphinxcontrib.kissapi as kissapi

name = "sphinxcontrib.kissapi"
# name = "test_package"

pkg = kissapi.introspect.analyze_package(name)
# for k,v in pkg.mods_tbl.items():
#     print(k)
#     print(v.members)
import sys
sys.path.append("./")
import sphinxcontrib.kissapi as kissapi

# dosa
sys.path.append("../rxperso-ml/dosa/")

name = "dosa"
# name = "sphinxcontrib.kissapi"
# name = "test_package"

pkg = kissapi.introspect.analyze_package(name)
# for k,v in pkg.mods_tbl.items():
#     print(k)
#     print(v.members)

p = pkg.mods_tbl["dosa.plugins"].members["Registry"]
for k,v in p.members.items():
    print(k,":", v.type, v.binding, v.reason)

m = pkg.mods_tbl["dosa.plugins"]
print(m.analyzer.find_attr_docs())
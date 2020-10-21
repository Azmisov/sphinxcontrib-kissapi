import sys
sys.path.append("./")
import sphinxcontrib.kissapi as kissapi

# dosa
sys.path.append("../rxperso-ml/dosa/")

pkg = kissapi.introspect.analyze_package("dosa")
print(pkg)
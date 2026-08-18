"""Catalogue — models, their geometry, and what printing one would take.

Public interface. Measures manufacturing, not money: the estimator returns a
:class:`PrintPrediction`, and the caller composes a pricing input from it, so this
context and ``pricing`` stay independent of each other.
"""

from printorian.contexts.catalog.assets import (
    ModelLibrary,
    assert_priceable,
    format_of,
    mesh_to_dict,
)
from printorian.contexts.catalog.browse import (
    DIFFICULTY_BANDS,
    CatalogPage,
    FacetCount,
    Facets,
    ModelCatalogue,
    SortKey,
)
from printorian.contexts.catalog.catalogue import (
    CatalogModel,
    CatalogModelMaterial,
    ModelCategory,
    SizeClass,
    size_class_of,
)
from printorian.contexts.catalog.catalogue_schemas import (
    CatalogCard,
    CatalogTable,
    FacetCountView,
    MeasuredPrint,
    card_of,
)
from printorian.contexts.catalog.estimator import (
    EstimationProfile,
    PrintPrediction,
    estimate,
    mm3_to_cm3,
    volume_to_mass,
)
from printorian.contexts.catalog.library import PlateLibrary
from printorian.contexts.catalog.mesh import (
    MeshAnalysis,
    MeshQuality,
    MeshWarning,
    analyse_stl,
)
from printorian.contexts.catalog.models import (
    ModelAsset,
    ModelFormat,
    PlateStatus,
    PreparedPlate,
)
from printorian.contexts.catalog.plate_file import PlateNumbers, read_plate
from printorian.contexts.catalog.plate_key import KEY_VERSION, plate_key
from printorian.contexts.catalog.schemas import (
    ModelAssetView,
    PreparedPlateView,
    RecordPlate,
)

__all__ = [
    "DIFFICULTY_BANDS",
    "KEY_VERSION",
    "CatalogCard",
    "CatalogModel",
    "CatalogModelMaterial",
    "CatalogPage",
    "CatalogTable",
    "EstimationProfile",
    "FacetCount",
    "FacetCountView",
    "Facets",
    "MeasuredPrint",
    "MeshAnalysis",
    "MeshQuality",
    "MeshWarning",
    "ModelAsset",
    "ModelAssetView",
    "ModelCatalogue",
    "ModelCategory",
    "ModelFormat",
    "ModelLibrary",
    "PlateLibrary",
    "PlateNumbers",
    "PlateStatus",
    "PreparedPlate",
    "PreparedPlateView",
    "PrintPrediction",
    "RecordPlate",
    "SizeClass",
    "SortKey",
    "analyse_stl",
    "assert_priceable",
    "card_of",
    "estimate",
    "format_of",
    "mesh_to_dict",
    "mm3_to_cm3",
    "plate_key",
    "read_plate",
    "size_class_of",
    "volume_to_mass",
]

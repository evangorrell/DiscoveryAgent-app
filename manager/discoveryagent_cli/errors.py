"""User-facing manager error types."""


class ManagerError(RuntimeError):
    """Base class for an expected manager failure."""


class StateError(ManagerError):
    """The installation state is missing or invalid."""


class ReleaseError(ManagerError):
    """The active release metadata or layout is invalid."""


class LaunchValidationError(ManagerError):
    """The installation is not ready for a future application launch."""


class RuntimeAcquisitionError(ManagerError):
    """An app-owned runtime could not be acquired or validated."""


class BootstrapError(ManagerError):
    """A development installation could not be constructed safely."""


class LaunchRuntimeError(ManagerError):
    """The packaged frontend could not be launched or reached safely."""


class MaintenanceError(ManagerError):
    """Update or uninstall could not proceed safely."""


class ActiveResearchError(MaintenanceError):
    """A live or potentially live research round prevents maintenance."""


class UpdateError(MaintenanceError):
    """An installed application update failed safely."""


class UninstallError(MaintenanceError):
    """The installed application could not be removed safely."""

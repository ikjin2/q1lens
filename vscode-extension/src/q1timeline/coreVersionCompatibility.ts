// @ts-nocheck
const SUPPORTED_CORE_VERSION_RANGE = "0.1.x or 0.2.x";

function isSupportedCoreVersion(version) {
  if (typeof version !== "string") {
    return false;
  }
  return /^0\.[12]\.\d+$/.test(version);
}

export {
  SUPPORTED_CORE_VERSION_RANGE,
  isSupportedCoreVersion,
};
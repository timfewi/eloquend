{
  lib,
  python3Packages,
  piper-tts ? null,
  withPiper ? piper-tts != null,
}:

assert lib.assertMsg (
  !withPiper || piper-tts != null
) "eloquend: withPiper requires pkgs.piper-tts";

let
  piperRuntime =
    if withPiper then
      piper-tts.override {
        withTrain = false;
        withHTTP = false;
        withAlignment = false;
      }
    else
      null;
in
python3Packages.buildPythonApplication {
  pname = "eloquend";
  version = "0.1.0";
  pyproject = true;

  src = lib.cleanSourceWith {
    src = ../.;
    filter =
      path: type:
      let
        name = baseNameOf path;
      in
      lib.cleanSourceFilter path type
      && name != ".direnv"
      && name != "__pycache__"
      && !lib.hasSuffix ".pyc" name;
  };

  build-system = [ python3Packages.setuptools ];

  dependencies = lib.optional withPiper piperRuntime;

  nativeCheckInputs = [ python3Packages.unittestCheckHook ];
  unittestFlags = [
    "-s"
    "tests"
    "-v"
  ];

  pythonImportsCheck = [
    "eloquend"
    "eloquend.backends"
  ];

  passthru = {
    inherit withPiper;
  };

  meta = {
    description = "Low-latency local streaming TTS daemon";
    mainProgram = "eloquend";
    platforms = lib.platforms.unix;
  };
}

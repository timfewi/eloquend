{
  config,
  lib,
  pkgs,
  ...
}:

let
  cfg = config.services.eloquend;

  command = [
    (lib.getExe cfg.package)
    "--backend"
    cfg.backend
    "--socket"
    cfg.socketPath
  ]
  ++ lib.optionals (cfg.model != null) [
    "--model"
    cfg.model
  ]
  ++ lib.optionals (cfg.backend == "openai") [
    "--voice"
    cfg.voice
    "--api-key-file"
    cfg.apiKeyFile
    "--base-url"
    cfg.baseUrl
    "--sample-rate"
    (toString cfg.sampleRate)
    "--request-timeout"
    (toString cfg.requestTimeout)
  ]
  ++ lib.optional cfg.useCuda "--cuda"
  ++ lib.optional (!cfg.warmup) "--no-warmup"
  ++ [
    "--first-chunk-chars"
    (toString cfg.tuning.firstChunkChars)
    "--min-chunk-chars"
    (toString cfg.tuning.minChunkChars)
    "--max-chunk-chars"
    (toString cfg.tuning.maxChunkChars)
    "--max-wait-ms"
    (toString cfg.tuning.maxWaitMs)
    "--segment-queue-size"
    (toString cfg.tuning.segmentQueueSize)
    "--audio-queue-size"
    (toString cfg.tuning.audioQueueSize)
  ]
  ++ cfg.extraArgs;
in
{
  options.services.eloquend = {
    enable = lib.mkEnableOption "the warm low-latency eloquend TTS daemon";

    package = lib.mkOption {
      type = lib.types.package;
      default = pkgs.callPackage ./package.nix { };
      defaultText = lib.literalExpression "pkgs.callPackage ./nix/package.nix { }";
      description = ''
        eloquend package. The default includes the maintained Piper runtime
        when pkgs.piper-tts exists in the pinned nixpkgs revision.
      '';
    };

    backend = lib.mkOption {
      type = lib.types.enum [
        "piper"
        "tone"
        "openai"
      ];
      default = "piper";
      description = ''
        Synthesis backend. The tone backend is only for tests. The openai
        backend speaks the OpenAI-compatible audio speech API and is meant
        for hosted providers such as OpenRouter.
      '';
    };

    model = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/nix/store/hash-de-voice/de_DE-voice-medium.onnx";
      description = ''
        Absolute Piper ONNX model path, or the hosted model slug when the
        openai backend is selected. A Piper model's JSON configuration must
        be available next to the model.
      '';
    };

    voice = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "de-DE-Klaus:MAI-Voice-2";
      description = "Provider voice identifier for the openai backend.";
    };

    apiKeyFile = lib.mkOption {
      type = lib.types.nullOr lib.types.str;
      default = null;
      example = "/run/secrets/openrouter-api-key";
      description = ''
        Runtime path to a file holding the provider API key. The daemon reads
        it once at startup; the value never enters the Nix store.
      '';
    };

    baseUrl = lib.mkOption {
      type = lib.types.str;
      default = "https://openrouter.ai/api/v1";
      description = ''
        OpenAI-compatible audio API base URL used by the openai backend.
      '';
    };

    sampleRate = lib.mkOption {
      type = lib.types.ints.positive;
      default = 24000;
      description = ''
        PCM sample rate returned by the hosted provider. The OpenAI audio
        format convention is 24 kHz mono signed 16-bit little-endian.
      '';
    };

    requestTimeout = lib.mkOption {
      type = lib.types.ints.positive;
      default = 60;
      description = "HTTP request timeout in seconds for the openai backend.";
    };

    socketPath = lib.mkOption {
      type = lib.types.str;
      default = "%t/eloquend.sock";
      description = "Unix socket path; systemd expands %t to the user runtime directory.";
    };

    warmup = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Run a discarded inference while the service starts.";
    };

    useCuda = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = ''
        Ask Piper to use CUDA. This requires a package overridden with a
        matching GPU-enabled ONNX Runtime closure.
      '';
    };

    tuning = {
      firstChunkChars = lib.mkOption {
        type = lib.types.ints.positive;
        default = 24;
        description = "Minimum buffered characters before first deadline flush.";
      };

      minChunkChars = lib.mkOption {
        type = lib.types.ints.positive;
        default = 40;
        description = "Minimum buffered characters after the first phrase.";
      };

      maxChunkChars = lib.mkOption {
        type = lib.types.ints.positive;
        default = 140;
        description = "Hard phrase-size limit.";
      };

      maxWaitMs = lib.mkOption {
        type = lib.types.ints.positive;
        default = 180;
        description = "Maximum segmentation wait after enough text is buffered.";
      };

      segmentQueueSize = lib.mkOption {
        type = lib.types.ints.positive;
        default = 4;
        description = "Bounded phrase queue size.";
      };

      audioQueueSize = lib.mkOption {
        type = lib.types.ints.positive;
        default = 8;
        description = "Bounded audio-frame queue size.";
      };
    };

    extraArgs = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ ];
      description = "Additional daemon arguments.";
    };
  };

  config = lib.mkIf cfg.enable {
    assertions = [
      {
        assertion = cfg.backend != "piper" || cfg.model != null;
        message = "services.eloquend.model is required for the Piper backend";
      }
      {
        assertion =
          cfg.backend != "openai" || (cfg.model != null && cfg.voice != null && cfg.apiKeyFile != null);
        message = "services.eloquend.model, voice and apiKeyFile are required for the openai backend";
      }
      {
        assertion =
          cfg.tuning.maxChunkChars >= cfg.tuning.firstChunkChars
          && cfg.tuning.maxChunkChars >= cfg.tuning.minChunkChars;
        message = "services.eloquend.tuning.maxChunkChars must cover both minimums";
      }
    ];

    systemd.user.services.eloquend = {
      Unit = {
        Description = "Warm low-latency streaming TTS daemon";
        After = [ "default.target" ];
      };

      Service = {
        Type = "simple";
        ExecStart = lib.escapeShellArgs command;
        Restart = "on-failure";
        RestartSec = "1s";
        TimeoutStopSec = "5s";
        Environment = [
          "PYTHONUNBUFFERED=1"
        ]
        ++ lib.optionals (cfg.backend == "openai") [
          "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
          "NIX_SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
        ];

        NoNewPrivileges = true;
        PrivateTmp = true;
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        RestrictAddressFamilies = [
          "AF_UNIX"
        ]
        ++ lib.optionals (cfg.backend == "openai") [
          "AF_INET"
          "AF_INET6"
        ];
        # The socket lives directly in %t. ProtectSystem=strict makes the
        # runtime directory read-only unless it is listed here.
        ReadWritePaths = [ "%t" ];
        UMask = "0077";
      };

      Install.WantedBy = [ "default.target" ];
    };
  };
}

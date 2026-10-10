{
  description = "cfe-scrapper — RPU in, receipt data out. Nix-reproducible scraper service.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachSystem [ "x86_64-linux" "aarch64-linux" ] (system:
      let
        pkgs = import nixpkgs { inherit system; };

        python = pkgs.python312.withPackages (ps: [
          ps.psycopg2 ps.pdfplumber ps.requests
        ]);

        # puppeteer-core pinned by hash: the same node_modules everywhere.
        puppeteer-core = pkgs.stdenvNoCC.mkDerivation {
          name = "puppeteer-core-23.11.1";
          dontUnpack = true;
          outputHashAlgo = "sha256";
          outputHashMode = "recursive";
          outputHash = "sha256-rcUS5Yr0gcVrNidsR1//LHqBdXZZecMXHjtd23pFDJU=";
          nativeBuildInputs = [ pkgs.nodejs_20 ];
          buildPhase = ''
            runHook preBuild
            mkdir -p $out
            cd $out
            npm init -y --silent >/dev/null
            npm install puppeteer-core@23.11.1 --silent
            rm -f package.json package-lock.json
            runHook postBuild
          '';
          installPhase = "true";
        };

        serviceTree = pkgs.stdenv.mkDerivation {
          name = "cfe-scrapper-service";
          src = ./service;
          installPhase = ''
            mkdir -p $out
            cp -r . $out
          '';
        };

        # Reproducible: inputs pinned via flake.lock, no build-time stamp.
        scraper-image = pkgs.dockerTools.buildImage {
          name = "ghcr.io/lopezpalacios/cfe-scrapper";
          tag = "latest";
          copyToRoot = pkgs.buildEnv {
            name = "image-root";
            paths = with pkgs; [
              python
              nodejs_20
              chromium
              zbar
              poppler-utils
              coreutils
              bash
            ];
            pathsToLink = [ "/bin" "/lib" "/share" ];
          };
          config = {
            WorkingDir = "/app";
            Cmd = [ "${python}/bin/python3" "/app/worker.py" ];
            Env = [
              "NODE_BIN=${pkgs.nodejs_20}/bin/node"
              "NODE_PATH=/app/node_modules"
              "CHROME_PATH=${pkgs.chromium}/bin/chromium"
              "PATH=${pkgs.lib.makeBinPath [ pkgs.python312 pkgs.nodejs_20 pkgs.zbar pkgs.poppler-utils pkgs.coreutils ]}"
            ];
          };
          extraCommands = ''
            mkdir -p app
            cp -r ${serviceTree}/. app/
            cp -r ${puppeteer-core} app/node_modules
            mkdir -p usr/bin
            ln -sf ${pkgs.chromium}/bin/chromium usr/bin/google-chrome
          '';
        };

      in {
        packages = {
          default = scraper-image;
          scraper-image = scraper-image;
          service = serviceTree;
        };

        devShells.default = pkgs.mkShell {
          buildInputs = [ python pkgs.nodejs_20 pkgs.zbar pkgs.poppler-utils pkgs.chromium ];
          shellHook = ''
            export CHROME_PATH=${pkgs.chromium}/bin/chromium
            export NODE_PATH=$(pwd)/node_modules
          '';
        };
      });
}

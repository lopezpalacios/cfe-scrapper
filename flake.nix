{
  description = "cfe-scrapper — RPU in, receipt data out. Nix-reproducible scraper service.";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    flake-utils.url = "github:numtide/flake-utils";
  };

  outputs = { self, nixpkgs, flake-utils }:
    flake-utils.lib.eachSystem [ "x86_64-linux" "aarch64-linux" "aarch64-darwin" ] (system:
      let
        pkgs = import nixpkgs { inherit system; };

        python = pkgs.python312.withPackages (ps: [
          ps.psycopg2 ps.pdfplumber ps.requests
        ]);

        serviceTree = pkgs.stdenv.mkDerivation {
          name = "cfe-scrapper-service";
          src = ./service;
          installPhase = ''
            mkdir -p $out
            cp -r worker.py decode_rpu.sh harvest $out/
          '';
        };

        # The reproducible image. The digest is a pure function of the flake
        # inputs + this source tree: what CI builds is what runs.
        scraper-image = pkgs.dockerTools.buildImage {
          name = "ghcr.io/lopezpalacios/cfe-scrapper";
          tag = "latest";
          created = "now";
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
              "CHROME_PATH=${pkgs.chromium}/bin/chromium"
              "PATH=${pkgs.lib.makeBinPath [ pkgs.python312 pkgs.nodejs_20 pkgs.zbar pkgs.poppler-utils pkgs.coreutils ]}"
            ];
          };
          extraCommands = ''
            mkdir -p app
            cp -r ${serviceTree}/. app/
            # legacy scripts reference /usr/bin/google-chrome
            ln -sf ${pkgs.chromium}/bin/chromium /usr/bin/google-chrome
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
          shellHook = "export CHROME_PATH=${pkgs.chromium}/bin/chromium";
        };
      });
}

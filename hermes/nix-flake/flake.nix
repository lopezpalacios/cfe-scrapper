{
  description = "Hermes Agent for the mini (newman): upstream Nous package, pinned + exposed as overlay";

  inputs = {
    nixpkgs.url = "flake:nixpkgs";
    hermes-agent.url = "github:NousResearch/hermes-agent/v0.21.6";
    hermes-agent.inputs.nixpkgs.follows = "nixpkgs";
  };

  outputs = { self, nixpkgs, hermes-agent }:
    let
      system = "aarch64-darwin";
    in
    {
      packages.${system} = {
        # messaging = minimal + the messaging extra (aiohttp, qrcode, …) —
        # what the WhatsApp gateway needs; full = all provider extras.
        hermes-agent = hermes-agent.packages.${system}.messaging;
        default = self.packages.${system}.hermes-agent;
      };
      overlays.default = final: prev: {
        hermes-agent = self.packages.${prev.system}.hermes-agent;
      };
    };
}

{
  description = "Sentinel Warehouse local research environment";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { nixpkgs, ... }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAllSystems = function:
        nixpkgs.lib.genAttrs systems (system: function nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            docker-compose
            duckdb
            git
            hyperfine
            jq
            postgresql_17
            python312
            uv
          ];

          shellHook = ''
            export UV_PYTHON="${pkgs.python312}/bin/python"
            export SENTINEL_RESEARCH_ROOT="$PWD/data/research"
            echo "Sentinel research shell: Python 3.12, uv, PostgreSQL 17, DuckDB"
          '';
        };
      });
    };
}

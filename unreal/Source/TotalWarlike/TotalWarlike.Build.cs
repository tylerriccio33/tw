using UnrealBuildTool;

public class TotalWarlike : ModuleRules
{
	public TotalWarlike(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"InputCore",
			// The sim bridge: FSocket lives in Sockets, FTcpSocketBuilder in
			// Networking, ISocketSubsystem in SocketSubsystem.
			"Sockets",
			"Networking",
			// The map layer: the baked geometry arrives as JSON, and every mesh
			// on screen is built at runtime rather than imported as an asset —
			// see Map/CampaignMap.h for why there is no Content/ to speak of.
			"Json",
			"ProceduralMeshComponent",
			// AddShaderSourceDirectoryMapping, so the terrain material's Custom
			// node can include /Project/TerrainCommon.ush.
			"RenderCore",
		});

		// Sim/MsgPack.cpp reports malformed frames by throwing, which is the only
		// sane thing a decoder can do mid-stream; the transport catches at the
		// one call site that owns the socket. Unreal disables exceptions by
		// default, so the module opts back in.
		bEnableExceptions = true;
	}
}

using UnrealBuildTool;

public class TotalWarlikeTarget : TargetRules
{
	public TotalWarlikeTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Game;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("TotalWarlike");
	}
}

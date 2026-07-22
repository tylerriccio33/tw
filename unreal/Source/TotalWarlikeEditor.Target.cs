using UnrealBuildTool;

public class TotalWarlikeEditorTarget : TargetRules
{
	public TotalWarlikeEditorTarget(TargetInfo Target) : base(Target)
	{
		Type = TargetType.Editor;
		DefaultBuildSettings = BuildSettingsVersion.Latest;
		IncludeOrderVersion = EngineIncludeOrderVersion.Latest;
		ExtraModuleNames.Add("TotalWarlike");
	}
}

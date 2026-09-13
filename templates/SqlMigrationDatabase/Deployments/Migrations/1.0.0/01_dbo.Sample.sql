-- Migration-Id: 20260612191625_112035b2

IF OBJECT_ID(N'[dbo].[Sample]', N'U') IS NULL
BEGIN
    CREATE TABLE [dbo].[Sample] (
        [SampleId] INT            NOT NULL IDENTITY (1, 1),
        [Name]     NVARCHAR (100) NOT NULL,
        CONSTRAINT [PK_Sample] PRIMARY KEY CLUSTERED ([SampleId] ASC)
    );
END;
